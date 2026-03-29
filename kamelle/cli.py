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
    from kamelle.config import (
        OPENCLAW_CONFIG_PATH,
        apply_models,
        backup_config,
        current_fallbacks,
        current_primary,
        extract_openrouter_base,
        format_for_list,
        format_for_primary,
        load_config,
        rollback_config,
        save_config,
    )
    from kamelle.openrouter import KamelleError, get_api_key, get_free_models, probe_model_latency
    from kamelle.ranking import rank_models
    from kamelle.state import (
        BACKUP_FILE,
        CACHE_FILE,
        DEFAULT_CACHE_HOURS,
        LATENCY_CACHE_HOURS,
        get_latency_entry,
        latency_entry_is_fresh,
        load_json,
        save_latency_entry,
    )
else:
    from .config import (
        OPENCLAW_CONFIG_PATH,
        apply_models,
        backup_config,
        current_fallbacks,
        current_primary,
        extract_openrouter_base,
        format_for_list,
        format_for_primary,
        load_config,
        rollback_config,
        save_config,
    )
    from .openrouter import KamelleError, get_api_key, get_free_models, probe_model_latency
    from .ranking import rank_models
    from .state import (
        BACKUP_FILE,
        CACHE_FILE,
        DEFAULT_CACHE_HOURS,
        LATENCY_CACHE_HOURS,
        get_latency_entry,
        latency_entry_is_fresh,
        load_json,
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


def require_api_key() -> str:
    api_key = get_api_key()
    if not api_key:
        fail("Kamelle couldn't find OPENROUTER_API_KEY. Set it in your environment or OpenClaw config first.")
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


def model_status_tag(model_id: str, primary: str | None, fallbacks: list[str]) -> str:
    if primary == format_for_primary(model_id):
        return "PRIMARY"
    if format_for_list(model_id) in set(fallbacks):
        return "FALLBACK"
    return ""


def print_plan(before: dict, after: dict) -> None:
    before_primary = current_primary(before)
    after_primary = current_primary(after)
    before_fallbacks = current_fallbacks(before)
    after_fallbacks = current_fallbacks(after)
    print(f"{SPARKLES['look']} Kamelle's plan")
    print("-" * 56)
    print(f"Primary:   {before_primary or 'not set'}")
    print(f"        -> {after_primary or 'not set'}")
    print(f"Fallbacks: {len(before_fallbacks)}")
    for fb in before_fallbacks[:10]:
        print(f"  - {fb}")
    print("      ->")
    for fb in after_fallbacks[:10]:
        print(f"  - {fb}")
    if len(after_fallbacks) > 10:
        print(f"  ... and {len(after_fallbacks) - 10} more")


def maybe_apply(before: dict, after: dict, dry_run: bool) -> None:
    print_plan(before, after)
    if dry_run:
        print(f"{SPARKLES['ok']} Dry run only. No candy was moved. {SPARKLES['bag']}")
        return
    backup_config(before)
    save_config(after)
    print(f"{SPARKLES['ok']} Applied. Backup saved at {BACKUP_FILE}")


def cmd_list(args):
    api_key = require_api_key()
    try:
        models = get_free_models(api_key, force_refresh=args.refresh)
    except KamelleError as exc:
        fail(str(exc))

    limit = args.limit
    config = load_config()
    primary = current_primary(config)
    fallbacks = current_fallbacks(config)

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
                "status": model_status_tag(m.id, primary, fallbacks) or None,
            })
        print(_json.dumps(output, indent=2))
        return

    print(f"{SPARKLES['look']} Kamelle scanned the sky for fresh free models...\n")
    print(f"Top {min(limit, len(models))} free models")
    print("-" * 112)
    print(f"{'#':<3} {'model':<54} {'ctx':<8} {'latency':<10} {'score':<7} {'status':<10}")
    print("-" * 112)
    for i, m in enumerate(visible, 1):
        tag = model_status_tag(m.id, primary, fallbacks)
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
    config = load_config()
    cache = load_json(CACHE_FILE, {})
    primary = current_primary(config)
    fallbacks = current_fallbacks(config)
    api_key = get_api_key()

    if getattr(args, "json", False):
        output = {
            "api_key_present": bool(api_key),
            "config_path": str(OPENCLAW_CONFIG_PATH),
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
    print(f"Config:    {OPENCLAW_CONFIG_PATH}")
    print(f"Primary:   {primary or 'not set'}")
    print(f"Fallbacks: {len(fallbacks)}")
    for fb in fallbacks[:10]:
        print(f"  - {fb}")
    print(f"Cache:     {CACHE_FILE}")
    print(f"Backup:    {BACKUP_FILE}")
    print(f"TTL:       {DEFAULT_CACHE_HOURS} hour")
    if cache.get('cached_at'):
        print(f"Cached at: {cache['cached_at']}")
        print(f"Models:    {cache.get('count', '?')}")


def cmd_doctor(args):
    print(f"{SPARKLES['tools']} Kamelle doctor")
    print("-" * 48)
    api_key = get_api_key()
    print(f"API key present:     {'yes' if api_key else 'no'}")
    print(f"OpenClaw config:     {'yes' if OPENCLAW_CONFIG_PATH.exists() else 'no'} ({OPENCLAW_CONFIG_PATH})")
    print(f"Cache file present:  {'yes' if CACHE_FILE.exists() else 'no'} ({CACHE_FILE})")
    print(f"Backup file present: {'yes' if BACKUP_FILE.exists() else 'no'} ({BACKUP_FILE})")

    try:
        OPENCLAW_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        probe = OPENCLAW_CONFIG_PATH.parent / '.kamelle-write-probe'
        probe.write_text('ok')
        probe.unlink()
        print("Config dir writable: yes")
    except Exception as exc:
        print(f"Config dir writable: no ({exc})")

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
    api_key = require_api_key()
    try:
        ranked = rank_models(get_free_models(api_key, force_refresh=args.refresh))
    except KamelleError as exc:
        fail(str(exc))
    if not ranked:
        fail("No free models found.")

    best = next((m for m in ranked if not m.is_router), ranked[0])
    fallback_ids = build_fallback_ids(ranked, None if args.keep_primary else best.id, args.fallback_count)
    before = load_config()

    if args.keep_primary:
        after = apply_models(before, None, fallback_ids)
        maybe_apply(before, after, args.dry_run)
        print(f"{SPARKLES['ok']} Kamelle kept your primary untouched and restocked your fallback bag. {SPARKLES['bag']}")
        return

    after = apply_models(before, best.id, fallback_ids)

    if getattr(args, "json", False):
        print(_json.dumps({
            "primary": current_primary(after),
            "fallbacks": current_fallbacks(after),
            "best_model": best.id,
            "best_score": round(best.score, 3),
            "dry_run": args.dry_run,
        }, indent=2))
        if not args.dry_run:
            backup_config(before)
            save_config(after)
        return

    maybe_apply(before, after, args.dry_run)
    print(f"{SPARKLES['ok']} Kamelle caught the best free candy for you. {SPARKLES['start']}")


def cmd_switch(args):
    api_key = require_api_key()
    try:
        ranked = rank_models(get_free_models(api_key, force_refresh=args.refresh))
    except KamelleError as exc:
        fail(str(exc))
    match = find_match(ranked, args.model)
    if not match:
        fail("Kamelle couldn't find that model in the free candy pile.")

    fallback_ids = [] if args.no_fallbacks else build_fallback_ids(ranked, match.id, args.fallback_count)
    before = load_config()
    after = apply_models(before, match.id, fallback_ids)
    maybe_apply(before, after, args.dry_run)
    print(f"{SPARKLES['ok']} Switched to {match.id}")


def cmd_fallbacks(args):
    api_key = require_api_key()
    try:
        ranked = rank_models(get_free_models(api_key, force_refresh=args.refresh))
    except KamelleError as exc:
        fail(str(exc))
    before = load_config()
    current = current_primary(before)
    current_id = extract_openrouter_base(current)
    fallback_ids = build_fallback_ids(ranked, current_id, args.count)
    after = apply_models(before, None, fallback_ids)
    maybe_apply(before, after, args.dry_run)
    print(f"{SPARKLES['ok']} Kamelle rebuilt your fallback bag. {SPARKLES['bag']}")


def cmd_rollback(args):
    if rollback_config():
        print(f"{SPARKLES['ok']} Rolled back to the last config snapshot. Sweet rescue. {SPARKLES['heart']}")
    else:
        fail("No backup found yet.")


def cmd_profile(args):
    profile_name = args.name.lower()
    if profile_name not in PROFILES:
        fail(f"Unknown profile '{profile_name}'. Choose: {', '.join(PROFILES.keys())}")

    api_key = require_api_key()
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

    fallback_ids = build_fallback_ids(ranked, primary_id, profile["fallback_count"])
    before = load_config()
    after = apply_models(before, primary_id, fallback_ids)
    maybe_apply(before, after, args.dry_run)
    print(f"{SPARKLES['ok']} Profile '{profile_name}' applied — {profile['desc']}. {SPARKLES['bag']}")


def cmd_watch(args):
    api_key = require_api_key()
    interval = args.interval * 60
    print(f"{SPARKLES['look']} Kamelle watch mode — checking every {args.interval} min. Ctrl+C to stop.")

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
                config = load_config()
                current = current_primary(config)
                best_formatted = format_for_primary(best.id)

                if current != best_formatted:
                    fallback_ids = build_fallback_ids(ranked, best.id, args.fallback_count)
                    before = config
                    after = apply_models(before, best.id, fallback_ids)
                    backup_config(before)
                    save_config(after)
                    print(f"{SPARKLES['bolt']} Rotated: {current} → {best_formatted} (score: {best.score:.3f})")
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kamelle",
        description="Kamelle helps you catch the best free OpenRouter models for OpenClaw.",
    )
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("list", help="List ranked free models")
    p.add_argument("-n", "--limit", type=int, default=15)
    p.add_argument("-r", "--refresh", action="store_true")
    p.add_argument("--probe-latency", action="store_true", help="Refresh latency numbers live for the listed models")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("refresh", help="Refresh the cached free model catalog")
    p.set_defaults(func=cmd_refresh)

    p = sub.add_parser("status", help="Show current Kamelle/OpenClaw model status")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("doctor", help="Check if Kamelle is ready to run")
    p.add_argument("--online", action="store_true", help="Also test a live OpenRouter fetch")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("auto", help="Pick the best free model and set fallbacks")
    p.add_argument("-r", "--refresh", action="store_true")
    p.add_argument("-c", "--fallback-count", type=int, default=5)
    p.add_argument("--keep-primary", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_auto)

    p = sub.add_parser("switch", help="Switch to a specific free model")
    p.add_argument("model")
    p.add_argument("-r", "--refresh", action="store_true")
    p.add_argument("-c", "--fallback-count", type=int, default=5)
    p.add_argument("--no-fallbacks", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_switch)

    p = sub.add_parser("fallbacks", help="Rebuild fallback models")
    p.add_argument("-r", "--refresh", action="store_true")
    p.add_argument("-c", "--count", type=int, default=5)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_fallbacks)

    p = sub.add_parser("rollback", help="Restore the last saved config backup")
    p.set_defaults(func=cmd_rollback)

    p = sub.add_parser("profile", help="Apply a preset profile (ultra/stabil/echo)")
    p.add_argument("name", choices=["ultra", "stabil", "echo"],
                    help="ultra=best quality, stabil=stable routing, echo=minimal")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_profile)

    p = sub.add_parser("watch", help="Auto-rotate to the best free model periodically")
    p.add_argument("-i", "--interval", type=int, default=30,
                    help="Check interval in minutes (default: 30)")
    p.add_argument("-c", "--fallback-count", type=int, default=5)
    p.set_defaults(func=cmd_watch)

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
