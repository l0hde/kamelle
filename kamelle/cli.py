from __future__ import annotations

import argparse
import json as _json
import signal
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

warnings.filterwarnings(
    "ignore",
    message=r"urllib3 v2 only supports OpenSSL 1\.1\.1\+",
)

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from kamelle.adapters import (
        DEFAULT_ADAPTER,
        AdapterError,
        AgentState,
        ModelPlan,
        adapter_names,
        detect_installed,
        get_adapter,
        same_model,
    )
    from kamelle.openrouter import KamelleError, get_api_key, get_free_models, probe_model_latency, bench_model
    from kamelle.ranking import rank_models
    from kamelle.state import (
        BENCH_CACHE_HOURS,
        CACHE_FILE,
        DEFAULT_CACHE_HOURS,
        HISTORY_FILE,
        KAMELLE_DIR,
        LATENCY_CACHE_HOURS,
        append_history_entry,
        bench_entry_is_fresh,
        get_bench_entry,
        get_latency_entry,
        latency_entry_is_fresh,
        load_all_bench_entries,
        load_json,
        save_bench_entry,
        save_latency_entry,
    )
else:
    from .adapters import (
        DEFAULT_ADAPTER,
        AdapterError,
        AgentState,
        ModelPlan,
        adapter_names,
        detect_installed,
        get_adapter,
        same_model,
    )
    from .openrouter import KamelleError, get_api_key, get_free_models, probe_model_latency, bench_model
    from .ranking import rank_models
    from .state import (
        BENCH_CACHE_HOURS,
        CACHE_FILE,
        DEFAULT_CACHE_HOURS,
        HISTORY_FILE,
        KAMELLE_DIR,
        LATENCY_CACHE_HOURS,
        append_history_entry,
        bench_entry_is_fresh,
        get_bench_entry,
        get_latency_entry,
        latency_entry_is_fresh,
        load_all_bench_entries,
        load_json,
        save_bench_entry,
        save_latency_entry,
    )

SPARKLES = {
    "start": "🍬",
    "ok": "✨",
    "warn": "⚠️",
    "bag": "👜",
    "look": "👀",
    "heart": "💖",
    "tools": "🛠️",
    "clock": "🕐",
    "bolt": "⚡",
}

PROFILES = {
    "ultra": {"primary_mode": "best", "fallback_count": 3, "probe": True, "desc": "Best quality — top model + 3 fallbacks"},
    "stabil": {"primary_mode": "router", "fallback_count": 5, "probe": False, "desc": "Stability — auto-routing + 5 fallbacks"},
    "echo": {"primary_mode": "router", "fallback_count": 0, "probe": False, "desc": "Minimal — auto-routing, no fallbacks"},
}


def fail(message: str, code: int = 1) -> None:
    print(f"{SPARKLES['warn']} {message}")
    raise SystemExit(code)


def say(adapter, message: str) -> None:
    """Print a status line, keeping stdout clean for adapters that write to it."""
    print(message, file=sys.stderr if adapter.writes_to_stdout else sys.stdout)


def build_adapter(args):
    """The agent backend this invocation targets (see `kamelle agents`)."""
    try:
        return get_adapter(
            getattr(args, "agent", None),
            getattr(args, "agent_config", None),
            getattr(args, "agent_option", None),
        )
    except AdapterError as exc:
        fail(str(exc))


def require_api_key(adapter=None) -> str:
    api_key = get_api_key(adapter)
    if not api_key:
        fail("Kamelle couldn't find OPENROUTER_API_KEY. Set it in your environment or your agent's config first.")
    return api_key


def format_context(n: int) -> str:
    if n >= 1_000_000:
        return f"{n // 1_000_000}M"
    if n >= 1_000:
        return f"{n // 1_000}K"
    return str(n)


def format_latency_label(entry: dict | None) -> str:
    if not entry or not latency_entry_is_fresh(entry):
        return "—"
    status = entry.get("status")
    latency_ms = entry.get("latency_ms")
    if status == "ok" and isinstance(latency_ms, int):
        return f"{latency_ms}ms"
    if status == "rate_limit":
        return "rl"
    if status == "timeout":
        return "timeout"
    if status == "unavailable":
        return "down"
    if isinstance(status, str) and status.startswith("http_"):
        return status.replace("http_", "http")
    return status or "—"


def maybe_probe_latencies(api_key: str, models, should_probe: bool):
    latency_map = {}
    to_probe = []
    for m in models:
        entry = get_latency_entry(m.id)
        if should_probe or not latency_entry_is_fresh(entry):
            to_probe.append(m)
        else:
            latency_map[m.id] = entry

    if to_probe:
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {pool.submit(probe_model_latency, api_key, m.id): m for m in to_probe}
            for future in as_completed(futures):
                m = futures[future]
                try:
                    status, latency_ms = future.result()
                    entry = save_latency_entry(m.id, status, latency_ms)
                except Exception:
                    entry = save_latency_entry(m.id, "error", None)
                latency_map[m.id] = entry

    return latency_map


def find_match(models, needle: str):
    needle = needle.lower()
    for m in models:
        if m.id.lower() == needle:
            return m
    for m in models:
        if needle in m.id.lower():
            return m
    return None


def build_fallback_ids(ranked_models, primary_id: str | None, count: int) -> list[str]:
    fallback_ids: list[str] = []
    if primary_id != "openrouter/free" and count > 0:
        fallback_ids.append("openrouter/free")

    for m in ranked_models:
        if len(fallback_ids) >= count:
            break
        if m.is_router:
            continue
        if primary_id and m.id == primary_id:
            continue
        fallback_ids.append(m.id)
    return fallback_ids


def print_plan(adapter, before: AgentState, after: AgentState, stream=sys.stdout) -> None:
    print(f"{SPARKLES['look']} Kamelle's plan for {adapter.display_name}", file=stream)
    print("-" * 56, file=stream)
    print(f"Config:    {adapter.location()}", file=stream)
    print(f"Primary:   {before.primary or 'not set'}", file=stream)
    print(f"        -> {after.primary or 'not set'}", file=stream)
    print(f"Fallbacks: {len(before.fallbacks)}", file=stream)
    for fb in before.fallbacks[:10]:
        print(f"  - {fb}", file=stream)
    print("      ->", file=stream)
    for fb in after.fallbacks[:10]:
        print(f"  - {fb}", file=stream)
    if len(after.fallbacks) > 10:
        print(f"  ... and {len(after.fallbacks) - 10} more", file=stream)


def maybe_apply(adapter, before, plan: ModelPlan, dry_run: bool, score=None, quiet: bool = False):
    """Show the plan, then hand it to the adapter. Returns the resulting state."""
    try:
        after = adapter.apply(before, plan)
    except AdapterError as exc:
        fail(str(exc))

    before_state = adapter.describe(before)
    after_state = adapter.describe(after)
    # An adapter that prints its result owns stdout; our chatter goes to stderr.
    stream = sys.stderr if adapter.writes_to_stdout else sys.stdout

    if not quiet:
        print_plan(adapter, before_state, after_state, stream)
        note = adapter.note_for(plan)
        if note:
            print(f"{SPARKLES['warn']} {note}", file=stream)

    if dry_run:
        if not quiet:
            print(f"{SPARKLES['ok']} Dry run only. No candy was moved. {SPARKLES['bag']}", file=stream)
        return after_state

    backup = adapter.backup(before)
    adapter.save(after)
    append_history_entry(before_state.primary, after_state.primary, score, agent=adapter.name)
    if not quiet:
        where = f" Backup saved at {backup}" if backup else ""
        print(f"{SPARKLES['ok']} Applied.{where}", file=stream)
    return after_state


def cmd_list(args):
    adapter = build_adapter(args)
    api_key = require_api_key(adapter)
    try:
        models = get_free_models(api_key, force_refresh=args.refresh)
    except KamelleError as exc:
        fail(str(exc))

    limit = args.limit
    state = adapter.describe(adapter.load())

    # Probe latencies first (parallel), then rank with latency data
    visible_for_probe = models[:limit]
    latency_map = maybe_probe_latencies(api_key, visible_for_probe, args.probe_latency)
    models = rank_models(models, latency_map)
    visible = models[:limit]

    if getattr(args, "json", False):
        output = []
        for i, m in enumerate(visible, 1):
            entry = latency_map.get(m.id)
            output.append({
                "rank": i,
                "id": m.id,
                "context_length": m.context_length,
                "score": round(m.score, 3),
                "latency_ms": entry.get("latency_ms") if entry else None,
                "latency_status": entry.get("status") if entry else None,
                "status": adapter.status_tag(state, m.id) or None,
            })
        print(_json.dumps(output, indent=2))
        return

    print(f"{SPARKLES['look']} Kamelle scanned the sky for fresh free models...\n")
    print(f"Top {min(limit, len(models))} free models")
    print("-" * 112)
    print(f"{'#':<3} {'model':<54} {'ctx':<8} {'latency':<10} {'score':<7} {'status':<10}")
    print("-" * 112)
    for i, m in enumerate(visible, 1):
        tag = adapter.status_tag(state, m.id)
        latency = format_latency_label(latency_map.get(m.id))
        print(f"{i:<3} {m.id[:54]:<54} {format_context(m.context_length):<8} {latency:<10} {m.score:<7.3f} {tag:<10}")
    print("-" * 112)
    print(f"{SPARKLES['bolt']} Latency shows the last local probe RTT when available (cache: {LATENCY_CACHE_HOURS}h).")
    if not args.probe_latency:
        print(f"{SPARKLES['clock']} Use --probe-latency to refresh latency numbers live.")
    print(f"{SPARKLES['ok']} Caught {len(models)} free models. Your bag is not empty. {SPARKLES['bag']}")


def cmd_refresh(args):
    api_key = require_api_key()
    try:
        models = rank_models(get_free_models(api_key, force_refresh=True))
    except KamelleError as exc:
        fail(str(exc))
    print(f"{SPARKLES['ok']} Kamelle refreshed the candy radar.")
    print(f"Found {len(models)} free models and updated the local cache at {CACHE_FILE}")


def cmd_status(args):
    adapter = build_adapter(args)
    snapshot = adapter.load()
    native = adapter.native(snapshot)
    cache = load_json(CACHE_FILE, {})
    api_key = get_api_key(adapter)
    primary = native.get("primary")
    fallbacks = native.get("fallbacks") or []

    if getattr(args, "json", False):
        output = {
            "api_key_present": bool(api_key),
            "agent": adapter.name,
            "config_path": adapter.location(),
            "primary": primary,
            "fallbacks": fallbacks,
            "cache_path": str(CACHE_FILE),
            "cached_at": cache.get("cached_at"),
            "cached_models": cache.get("count"),
            "cache_ttl_hours": DEFAULT_CACHE_HOURS,
        }
        print(_json.dumps(output, indent=2))
        return

    print(f"{SPARKLES['heart']} Kamelle status")
    print("-" * 48)
    print(f"API key:   {'present' if api_key else 'missing'}")
    print(f"Agent:     {adapter.display_name}")
    print(f"Config:    {adapter.location()}")
    print(f"Primary:   {primary or 'not set'}")
    print(f"Fallbacks: {len(fallbacks)}")
    for fb in fallbacks[:10]:
        print(f"  - {fb}")
    print(f"Cache:     {CACHE_FILE}")
    print(f"Backup:    {adapter.backup_path()}")
    print(f"TTL:       {DEFAULT_CACHE_HOURS} hour")
    if cache.get('cached_at'):
        print(f"Cached at: {cache['cached_at']}")
        print(f"Models:    {cache.get('count', '?')}")


def cmd_doctor(args):
    adapter = build_adapter(args)
    backup_path = adapter.backup_path()
    print(f"{SPARKLES['tools']} Kamelle doctor")
    print("-" * 48)
    api_key = get_api_key(adapter)
    print(f"API key present:     {'yes' if api_key else 'no'}")
    print(f"Agent:               {adapter.display_name} ({adapter.name})")
    print(f"Agent config:        {'yes' if adapter.is_installed() else 'no'} ({adapter.location()})")
    print(f"Cache file present:  {'yes' if CACHE_FILE.exists() else 'no'} ({CACHE_FILE})")
    print(f"Backup file present: {'yes' if backup_path.exists() else 'no'} ({backup_path})")

    for label, directory in (("Kamelle dir", KAMELLE_DIR), ("Config dir", adapter.config_path.parent if adapter.config_path else None)):
        if directory is None:
            print(f"{label + ' writable:':<21}n/a (this agent writes to stdout)")
            continue
        try:
            directory.mkdir(parents=True, exist_ok=True)
            probe = directory / '.kamelle-write-probe'
            probe.write_text('ok')
            probe.unlink()
            print(f"{label + ' writable:':<21}yes")
        except Exception as exc:
            print(f"{label + ' writable:':<21}no ({exc})")

    installed = detect_installed()
    if installed:
        print(f"Agents detected:     {', '.join(a.display_name for a in installed)}")

    if args.online:
        if not api_key:
            fail("Doctor online check skipped: API key missing.")
        try:
            models = rank_models(get_free_models(api_key, force_refresh=True))
            print(f"Online fetch:        yes ({len(models)} free models)")
        except KamelleError as exc:
            print(f"Online fetch:        no ({exc})")
            raise SystemExit(1)

    print(f"{SPARKLES['ok']} Doctor finished.")


def cmd_auto(args):
    adapter = build_adapter(args)
    api_key = require_api_key(adapter)
    try:
        ranked = rank_models(get_free_models(api_key, force_refresh=args.refresh))
    except KamelleError as exc:
        fail(str(exc))
    if not ranked:
        fail("No free models found.")

    best = next((m for m in ranked if not m.is_router), ranked[0])
    plan = ModelPlan(
        primary=None if args.keep_primary else best.id,
        fallbacks=build_fallback_ids(ranked, None if args.keep_primary else best.id, args.fallback_count),
    )
    before = adapter.load()

    if args.keep_primary:
        maybe_apply(adapter, before, plan, args.dry_run, score=None)
        say(adapter, f"{SPARKLES['ok']} Kamelle kept your primary untouched and restocked your fallback bag. {SPARKLES['bag']}")
        return

    if getattr(args, "json", False):
        after = adapter.apply(before, plan)
        native = adapter.native(after)
        print(_json.dumps({
            "agent": adapter.name,
            "primary": native.get("primary"),
            "fallbacks": native.get("fallbacks"),
            "best_model": best.id,
            "best_score": round(best.score, 3),
            "dry_run": args.dry_run,
        }, indent=2))
        if not args.dry_run:
            maybe_apply(adapter, before, plan, dry_run=False, score=best.score, quiet=True)
        return

    maybe_apply(adapter, before, plan, args.dry_run, score=best.score)
    say(adapter, f"{SPARKLES['ok']} Kamelle caught the best free candy for you. {SPARKLES['start']}")


def cmd_switch(args):
    adapter = build_adapter(args)
    api_key = require_api_key(adapter)
    try:
        ranked = rank_models(get_free_models(api_key, force_refresh=args.refresh))
    except KamelleError as exc:
        fail(str(exc))
    match = find_match(ranked, args.model)
    if not match:
        fail("Kamelle couldn't find that model in the free candy pile.")

    plan = ModelPlan(
        primary=match.id,
        fallbacks=[] if args.no_fallbacks else build_fallback_ids(ranked, match.id, args.fallback_count),
    )
    maybe_apply(adapter, adapter.load(), plan, args.dry_run, score=match.score)
    say(adapter, f"{SPARKLES['ok']} Switched to {match.id}")


def cmd_fallbacks(args):
    adapter = build_adapter(args)
    api_key = require_api_key(adapter)
    try:
        ranked = rank_models(get_free_models(api_key, force_refresh=args.refresh))
    except KamelleError as exc:
        fail(str(exc))
    before = adapter.load()
    current_id = adapter.describe(before).primary
    plan = ModelPlan(primary=None, fallbacks=build_fallback_ids(ranked, current_id, args.count))
    maybe_apply(adapter, before, plan, args.dry_run, score=None)
    say(adapter, f"{SPARKLES['ok']} Kamelle rebuilt your fallback bag. {SPARKLES['bag']}")


def cmd_rollback(args):
    adapter = build_adapter(args)
    if adapter.restore():
        print(f"{SPARKLES['ok']} Rolled back {adapter.display_name} to the last config snapshot. "
              f"Sweet rescue. {SPARKLES['heart']}")
    else:
        fail(f"No backup found yet for {adapter.display_name} ({adapter.backup_path()}).")


def cmd_agents(args):
    active = build_adapter(args)
    if getattr(args, "json", False):
        print(_json.dumps([
            {
                "name": adapter.name,
                "display_name": adapter.display_name,
                "config_path": adapter.location(),
                "installed": adapter.is_installed(),
                "active": adapter.name == active.name,
            }
            for adapter in (get_adapter(name) for name in adapter_names())
        ], indent=2))
        return

    print(f"{SPARKLES['tools']} Agents Kamelle can stock")
    print("-" * 86)
    print(f"{'':<2} {'name':<10} {'found':<7} {'writes':<52}")
    print("-" * 86)
    for name in adapter_names():
        adapter = get_adapter(name)
        marker = "→" if name == active.name else " "
        found = "yes" if adapter.is_installed() else "no"
        print(f"{marker:<2} {name:<10} {found:<7} {adapter.summary[:52]:<52}")
    print("-" * 86)
    print(f"Pick one with --agent NAME, or set KAMELLE_AGENT. Default: {DEFAULT_ADAPTER}.")


def cmd_profile(args):
    profile_name = args.name.lower()
    if profile_name not in PROFILES:
        fail(f"Unknown profile '{profile_name}'. Choose: {', '.join(PROFILES.keys())}")

    adapter = build_adapter(args)
    api_key = require_api_key(adapter)
    profile = PROFILES[profile_name]

    try:
        ranked = rank_models(get_free_models(api_key, force_refresh=True))
    except KamelleError as exc:
        fail(str(exc))

    if not ranked:
        fail("No free models found.")

    if profile["primary_mode"] == "best":
        best = next((m for m in ranked if not m.is_router), ranked[0])
        primary_id = best.id
    else:
        primary_id = "openrouter/free"

    plan = ModelPlan(
        primary=primary_id,
        fallbacks=build_fallback_ids(ranked, primary_id, profile["fallback_count"]),
    )
    maybe_apply(
        adapter,
        adapter.load(),
        plan,
        args.dry_run,
        score=best.score if profile["primary_mode"] == "best" else None,
    )
    say(adapter, f"{SPARKLES['ok']} Profile '{profile_name}' applied — {profile['desc']}. {SPARKLES['bag']}")


def cmd_watch(args):
    adapter = build_adapter(args)
    api_key = require_api_key(adapter)
    interval = args.interval * 60
    print(f"{SPARKLES['look']} Kamelle watch mode for {adapter.display_name} "
          f"— checking every {args.interval} min. Ctrl+C to stop.")

    running = True

    def handle_sigint(sig, frame):
        nonlocal running
        running = False
        print(f"\n{SPARKLES['ok']} Kamelle watch stopped.")

    signal.signal(signal.SIGINT, handle_sigint)

    while running:
        try:
            ranked = rank_models(get_free_models(api_key, force_refresh=True))
            if not ranked:
                print(f"{SPARKLES['warn']} No free models found. Waiting...")
            else:
                best = next((m for m in ranked if not m.is_router), ranked[0])
                before = adapter.load()
                current = adapter.describe(before).primary

                if not same_model(current, best.id):
                    plan = ModelPlan(
                        primary=best.id,
                        fallbacks=build_fallback_ids(ranked, best.id, args.fallback_count),
                    )
                    maybe_apply(adapter, before, plan, dry_run=False, score=best.score, quiet=True)
                    print(f"{SPARKLES['bolt']} Rotated: {current or 'not set'} → {best.id} "
                          f"(score: {best.score:.3f})")
                else:
                    print(f"{SPARKLES['ok']} Current best is still {best.id} (score: {best.score:.3f})")
        except KamelleError as exc:
            print(f"{SPARKLES['warn']} Error during check: {exc}")
        except Exception as exc:
            print(f"{SPARKLES['warn']} Unexpected error: {exc}")

        for _ in range(interval):
            if not running:
                break
            time.sleep(1)

    raise SystemExit(0)


def cmd_history(args):
    entries = load_json(HISTORY_FILE, [])
    if not entries:
        print(f"{SPARKLES['look']} No model switches recorded yet.")
        return
    visible = list(reversed(entries[-args.n:]))
    print(f"{SPARKLES['look']} Last {len(visible)} model switches\n")
    print(f"{'#':<3} {'timestamp':<22} {'agent':<10} {'from':<36} {'to':<36} {'score':<7}")
    print("-" * 118)
    for i, e in enumerate(visible, 1):
        ts = e.get('ts', '?')[:19]
        # Entries written before Kamelle spoke to more than one agent have no agent.
        agent = (e.get('agent') or '—')[:10]
        frm = (e.get('from') or '—')[:36]
        to = (e.get('to') or '—')[:36]
        sc = f"{e['score']:.3f}" if e.get('score') is not None else '—'
        print(f"{i:<3} {ts:<22} {agent:<10} {frm:<36} {to:<36} {sc:<7}")



def cmd_bench(args):
    """Test actual response quality for the top N free models."""
    api_key = require_api_key()

    if args.force_refresh:
        try:
            models = rank_models(get_free_models(api_key, force_refresh=True))
        except KamelleError as exc:
            fail(str(exc))
    else:
        try:
            models = rank_models(get_free_models(api_key))
        except KamelleError as exc:
            fail(str(exc))

    candidates = [m for m in models if not m.is_router][: args.n]
    if not candidates:
        fail("No free models available to bench.")

    if args.dry_run:
        print(f"{SPARKLES['look']} Dry run — would bench {len(candidates)} models:")
        for m in candidates:
            print(f"  {m.id}")
        return

    print(f"{SPARKLES['start']} Benching top {len(candidates)} free models ...\n")

    results: list[tuple] = []  # (model_id, entry)

    def run_bench(m):
        cached = get_bench_entry(m.id)
        if not args.no_cache and bench_entry_is_fresh(cached, hours=BENCH_CACHE_HOURS):
            return m.id, cached, True  # (id, entry, from_cache)
        entry = bench_model(api_key, m.id, timeout_seconds=args.timeout)
        save_bench_entry(m.id, entry)
        return m.id, entry, False

    with ThreadPoolExecutor(max_workers=min(args.parallel, len(candidates))) as pool:
        futures = {pool.submit(run_bench, m): m for m in candidates}
        completed = 0
        for future in as_completed(futures):
            completed += 1
            try:
                model_id, entry, from_cache = future.result()
                results.append((model_id, entry, from_cache))
                status_icon = SPARKLES['ok'] if entry['status'] == 'ok' else SPARKLES['warn']
                cached_note = " (cached)" if from_cache else ""
                print(f"  {status_icon} [{completed}/{len(candidates)}] {model_id}{cached_note}")
            except Exception as exc:
                model_id = futures[future].id
                results.append((model_id, {"status": "error", "latency_ms": None,
                                            "score": 0, "passes_q1": False, "passes_q2": False,
                                            "response": None}, False))
                print(f"  {SPARKLES['warn']} [{completed}/{len(candidates)}] {model_id}: {exc}")

    # Sort by (bench score desc, latency asc)
    results.sort(key=lambda x: (-x[1].get('score', 0),
                                 x[1].get('latency_ms') or 99999))

    if args.json:
        out = []
        for model_id, entry, _ in results:
            out.append({"model": model_id, **entry})
        print(_json.dumps(out, indent=2))
        return

    print(f"\n{SPARKLES['look']} Bench Results — top {len(results)} free models\n")
    hdr = f"{'#':<3} {'model':<45} {'status':<12} {'latency':>8}  {'Q1':>3} {'Q2':>3} {'score':>5}"
    print(hdr)
    print("-" * len(hdr))
    for i, (model_id, entry, from_cache) in enumerate(results, 1):
        status = entry.get('status', '?')
        lat = entry.get('latency_ms')
        lat_s = f"{lat}ms" if lat is not None else "—"
        q1 = "✓" if entry.get('passes_q1') else "✗"
        q2 = "✓" if entry.get('passes_q2') else "✗"
        sc = entry.get('score', 0)
        cache_flag = "*" if from_cache else " "
        short_id = model_id[:44]
        print(f"{i:<3} {short_id:<45} {status:<12} {lat_s:>8}  {q1:>3} {q2:>3} {sc:>5}{cache_flag}")

    print(f"\nQ1 = 17×4=68   Q2 = capital of Germany = Berlin")
    full_pass = sum(1 for _, e, _ in results if e.get('score', 0) == 2)
    print(f"{SPARKLES['ok']} {full_pass}/{len(results)} models passed both checks. * = cached result.")


def agent_flags() -> argparse.ArgumentParser:
    """Flags shared by every command that reads or writes an agent's config."""
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "-a", "--agent", default=None, choices=adapter_names(),
        help=f"Agent to stock (default: $KAMELLE_AGENT, else {DEFAULT_ADAPTER})",
    )
    parent.add_argument(
        "--agent-config", default=None, metavar="PATH",
        help="Use this config file instead of the agent's default location",
    )
    parent.add_argument(
        "-o", "--agent-option", action="append", default=None, metavar="KEY=VALUE",
        help="Adapter-specific option, repeatable (e.g. -o out=models.yaml)",
    )
    return parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kamelle",
        description="Kamelle helps you catch the best free OpenRouter models "
                    "for OpenClaw, Hermes and other AI agents.",
    )
    sub = parser.add_subparsers(dest="command")
    agent = agent_flags()

    p = sub.add_parser("agents", parents=[agent], help="List the agent backends Kamelle can write to")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_agents)

    p = sub.add_parser("list", parents=[agent], help="List ranked free models")
    p.add_argument("-n", "--limit", type=int, default=15)
    p.add_argument("-r", "--refresh", action="store_true")
    p.add_argument("--probe-latency", action="store_true", help="Refresh latency numbers live for the listed models")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("refresh", help="Refresh the cached free model catalog")
    p.set_defaults(func=cmd_refresh)

    p = sub.add_parser("status", parents=[agent], help="Show the agent's current model selection")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("doctor", parents=[agent], help="Check if Kamelle is ready to run")
    p.add_argument("--online", action="store_true", help="Also test a live OpenRouter fetch")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("auto", parents=[agent], help="Pick the best free model and set fallbacks")
    p.add_argument("-r", "--refresh", action="store_true")
    p.add_argument("-c", "--fallback-count", type=int, default=5)
    p.add_argument("--keep-primary", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_auto)

    p = sub.add_parser("switch", parents=[agent], help="Switch to a specific free model")
    p.add_argument("model")
    p.add_argument("-r", "--refresh", action="store_true")
    p.add_argument("-c", "--fallback-count", type=int, default=5)
    p.add_argument("--no-fallbacks", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_switch)

    p = sub.add_parser("fallbacks", parents=[agent], help="Rebuild fallback models")
    p.add_argument("-r", "--refresh", action="store_true")
    p.add_argument("-c", "--count", type=int, default=5)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_fallbacks)

    p = sub.add_parser("rollback", parents=[agent], help="Restore the last saved config backup")
    p.set_defaults(func=cmd_rollback)

    p = sub.add_parser("profile", parents=[agent], help="Apply a preset profile (ultra/stabil/echo)")
    p.add_argument("name", choices=["ultra", "stabil", "echo"],
                    help="ultra=best quality, stabil=stable routing, echo=minimal")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_profile)

    p = sub.add_parser("bench", help="Test real response quality for the top N free models")
    p.add_argument("-n", "--n", type=int, default=5, help="Number of models to bench (default: 5)")
    p.add_argument("-t", "--timeout", type=int, default=20, help="Per-model timeout in seconds (default: 20)")
    p.add_argument("-p", "--parallel", type=int, default=3, help="Parallel workers (default: 3)")
    p.add_argument("-r", "--force-refresh", action="store_true", help="Force-refresh model list before benching")
    p.add_argument("--no-cache", action="store_true", help="Ignore cached bench results, re-bench all")
    p.add_argument("--dry-run", action="store_true", help="Show which models would be benched, then exit")
    p.add_argument("--json", action="store_true", help="Output results as JSON")
    p.set_defaults(func=cmd_bench)

    p = sub.add_parser("watch", parents=[agent], help="Auto-rotate to the best free model periodically")
    p.add_argument("-i", "--interval", type=int, default=30,
                    help="Check interval in minutes (default: 30)")
    p.add_argument("-c", "--fallback-count", type=int, default=5)
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("history", help="Show recent model switches")
    p.add_argument("-n", type=int, default=10)
    p.set_defaults(func=cmd_history)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if not getattr(args, "command", None):
        parser.print_help()
        raise SystemExit(1)
    args.func(args)


if __name__ == "__main__":
    main()
