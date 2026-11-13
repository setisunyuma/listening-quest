"""`lq` command line: generator operations (doctor, validate, plan, generate, review, approve, render,
build, import, demo) plus operations (migrate, create-user, seed, worker, outbox, gc, openapi)."""

from __future__ import annotations

import getpass
import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

import typer
from sqlalchemy import select

from lq.config import get_settings
from lq.db import models as m
from lq.db.base import new_id, session_scope

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Listening Quest generator and operations CLI")
outbox_app = typer.Typer(help="Persistent outbox for sending material to a Listening Quest server")
app.add_typer(outbox_app, name="outbox")


def _echo(obj) -> None:
    typer.echo(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _find_unit(s, ref: str, realm: str) -> m.Unit:
    unit = s.get(m.Unit, ref)
    if unit is None:
        unit = (
            s.execute(
                select(m.Unit).where(m.Unit.realm == realm, m.Unit.content_id == ref).order_by(m.Unit.revision.desc())
            )
            .scalars()
            .first()
        )
    if unit is None:
        typer.echo(f"unit not found: {ref}", err=True)
        raise typer.Exit(1)
    return unit


@app.command("config")
def show_config() -> None:
    """Show the effective settings and which .env file was read. Secret values are never printed."""
    from lq.config import describe_settings

    _echo(describe_settings())


@app.command()
def doctor() -> None:
    """Check FFmpeg, database, storage, provider configuration and price table."""
    from lq.generator import budget
    from lq.storage import get_storage

    s = get_settings()
    report: dict = {
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "ffprobe": bool(shutil.which("ffprobe")),
        "env": s.env,
        "storage_backend": s.storage_backend,
    }
    try:
        with session_scope() as db:
            db.execute(select(1))
            report["database"] = "ok"
            report["users"] = {r: db.query(m.User).filter(m.User.realm == r).count() for r in ("live", "demo")}
            report["paid_generation_enabled"] = budget.get_system(db, "live")["paid_enabled"]
            report["spend_today"] = budget.spent_today(db, "live")
    except Exception as e:  # noqa: BLE001
        report["database"] = f"error: {e}"
    try:
        st = get_storage()
        st.put("doctor/probe.txt", b"ok")
        report["storage"] = "ok" if st.get("doctor/probe.txt") == b"ok" else "mismatch"
        st.delete("doctor/probe.txt")
    except Exception as e:  # noqa: BLE001
        report["storage"] = f"error: {e}"
    table = budget.price_table()
    report["openai_api_key"] = "set" if s.openai_api_key else "not set (demo and import still work)"
    report["models"] = {
        "content": s.content_model,
        "review": s.review_model,
        "tts": f"{s.tts_provider}:{s.tts_model if s.tts_provider != 'openai_speech' else s.tts_speech_model}",
        "asr": s.asr_model,
    }
    report["prices_known"] = {
        "content": s.content_model in table["text"],
        "review": s.review_model in table["text"],
        "tts": s.tts_model in table["realtime"] or s.tts_speech_model in table["speech"],
        "asr": s.asr_model in table["transcription"],
    }
    report["price_table"] = {"source": table["source"], "checked_at": table["checked_at"]}
    if s.tts_provider == "openai_speech":
        report["warning"] = "openai_speech (/v1/audio/speech) is deprecated and shuts down on 2027-01-06"
    report["server_url"] = s.server_url or "not set (generate does not auto-send)"
    _echo(report)
    if not (report["ffmpeg"] and report["ffprobe"] and report["database"] == "ok" and report["storage"] == "ok"):
        raise typer.Exit(1)


@app.command()
def migrate() -> None:
    """Apply database migrations (alembic upgrade head) and register mode profiles."""
    from alembic.config import Config

    from alembic import command
    from lq.services import seed as seed_mod

    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(cfg, "head")
    with session_scope() as s:
        n = seed_mod.seed_profiles(s)
    typer.echo(f"migrations applied; profiles updated: {n}")


@app.command("create-user")
def create_user(
    username: str,
    realm: str = "live",
    password_env: str = typer.Option("", help="read the password from this environment variable instead of prompting"),
) -> None:
    """Create the owner account. No password is bundled: you type it, or pass it through an env var."""
    from lq.services import auth

    if password_env:
        pw = os.environ.get(password_env, "")
    else:
        pw = getpass.getpass("New password (10+ characters): ")
        if pw != getpass.getpass("Repeat password: "):
            typer.echo("passwords do not match", err=True)
            raise typer.Exit(1)
    try:
        with session_scope() as s:
            auth.create_user(s, username, pw, realm)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    typer.echo(f"created {realm} user {username!r}")


@app.command("set-password")
def set_password(username: str, password_env: str = "") -> None:
    """Change a password and sign out all sessions of that user."""
    from sqlalchemy import delete

    from lq.services import auth

    pw = os.environ.get(password_env, "") if password_env else getpass.getpass("New password (10+ characters): ")
    with session_scope() as s:
        user = s.execute(select(m.User).where(m.User.username == username)).scalar_one_or_none()
        if user is None:
            typer.echo("no such user", err=True)
            raise typer.Exit(1)
        user.password_hash = auth.hash_password(pw)
        s.execute(delete(m.AuthSession).where(m.AuthSession.user_id == user.id))
    typer.echo("password changed")


@app.command()
def seed(realm: str = "live") -> None:
    """Register the bundled example units (idempotent). In the live area they wait for real audio."""
    from lq.services import seed as seed_mod

    with session_scope() as s:
        seed_mod.seed_profiles(s)
        _echo(seed_mod.seed_fixtures(s, realm))


@app.command()
def demo(
    username: str = "demo",
    password_env: str = "",
    mock: bool = True,
    mock_modes: str = typer.Option("common_test,cefr_c1", help="modes to prepare an assessment set for"),
) -> None:
    """Build the demo data area with free demo providers and run all its jobs. Tone audio only
    (音声動作確認用); nothing is added to the live area."""
    from lq.jobs import worker
    from lq.services import seed as seed_mod

    with session_scope() as s:
        seed_mod.seed_profiles(s)
        out = seed_mod.seed_demo(
            s,
            username=username,
            password=os.environ.get(password_env) if password_env else None,
            with_mock=mock,
            mock_modes=tuple(x for x in mock_modes.split(",") if x),
        )
    outcomes = worker.drain("demo-cli")
    with session_scope() as s:
        out["mock_sets_assembled"] = len(seed_mod.assemble_demo_mocks(s))
        out["ready_units"] = s.query(m.Unit).filter(m.Unit.realm == "demo", m.Unit.status == "READY").count()
    out["job_outcomes"] = {k: outcomes.count(k) for k in sorted(set(outcomes))}
    out["note"] = "demo audio is tone bursts for checking playback, not listening material"
    _echo(out)
    if out["password_created"]:
        typer.echo(
            f"\nDemo login: {username} / {out['password_created']}  (shown once; change it with `lq set-password {username}`)"
        )


@app.command()
def validate(paths: list[Path]) -> None:
    """Validate Unit JSON / manifests against the schema and format rules. No DB or network."""
    from pydantic import ValidationError

    from lq.domain.content import UnitContent, content_hash
    from lq.generator.authoring import lint_unit
    from lq.services.importer import adapt_legacy_v1

    bad = 0
    for p in paths:
        data = json.loads(p.read_text(encoding="utf-8"))
        if data.get("schema_version") == 1:
            entries, notes = adapt_legacy_v1(data)
            units = [e["unit"] for e in entries]
            for n in notes:
                typer.echo(f"{p.name}: {n['item']}: {n['status']} - {n['reason']}")
        else:
            units = (
                [{k: v for k, v in u.items() if k not in ("audio", "review")} for u in data["units"]]
                if "units" in data
                else [{k: v for k, v in data.items() if k not in ("audio", "review", "files")}]
            )
        for raw in units:
            try:
                u = UnitContent.model_validate(raw)
            except ValidationError as e:
                bad += 1
                typer.echo(f"{p.name}: {raw.get('content_id')}: INVALID\n{e}")
                continue
            errs = [] if u.legacy_radio_only else lint_unit(u, require_distractors=data.get("schema_version") != 1)
            bad += 1 if errs else 0
            typer.echo(
                f"{p.name}: {u.content_id}@{u.revision}: {'FORMAT PROBLEMS ' + str(errs) if errs else 'ok'}  hash={content_hash(u)[:12]}"
            )
    raise typer.Exit(1 if bad else 0)


def _specs(mode: str, part: str, skill: str, band: int, count: int, topic: str, pool: str, source: str) -> list[dict]:
    from lq.domain.content import MODES, SKILLS
    from lq.domain.profiles import current_profile, part_spec

    if mode not in MODES or skill not in SKILLS:
        typer.echo(f"mode must be one of {MODES}; skill one of {SKILLS}", err=True)
        raise typer.Exit(1)
    p = part_spec(current_profile(mode), part)
    if p is None:
        typer.echo(f"unknown part {part!r}; parts: {[x['part'] for x in current_profile(mode)['parts']]}", err=True)
        raise typer.Exit(1)
    batch = new_id()[:8]
    return [
        {
            "mode": mode,
            "part": part,
            "template_type": p["template_type"],
            "primary_skill": skill,
            "band": band,
            "topic_hint": topic,
            "seed": f"cli-{batch}-{i}",
            "source": source,
            "pool": pool,
        }
        for i in range(count)
    ]


@app.command()
def plan(mode: str, part: str, skill: str = "explicit_detail", band: int = 3, count: int = 1) -> None:
    """Show what `generate` would do and a cost ESTIMATE. Calls no API."""
    from lq.audio.render import plan_clips
    from lq.generator import authoring, budget
    from lq.generator.providers import GenSpec
    from lq.services.seed import load_fixture_units

    s = get_settings()
    specs = _specs(mode, part, skill, band, count, "", "practice", "generated")
    system, user = authoring.build_generation_prompt(GenSpec(**specs[0]))
    words = authoring.templates()["modes"][mode][part]["words"][1]
    sample = next((u for u in load_fixture_units() if u.mode == mode and u.part == part), None)
    clips = len(plan_clips(sample)) if sample else 10
    chars = words * 6 + clips * 60
    est = {
        "content": budget.est_text(
            s.content_model, len(system) + len(user) + len(json.dumps(authoring.authored_schema())), 6000
        ),
        "review": budget.est_text(s.review_model, words * 8 + 1500, 2500),
        "tts": budget.est_tts(
            "speech" if s.tts_provider == "openai_speech" else "realtime",
            s.tts_speech_model if s.tts_provider == "openai_speech" else s.tts_model,
            "x" * chars,
        ),
        "asr": budget.est_asr(s.asr_model, chars / 14),
    }
    per_unit = None if any(v is None for v in est.values()) else round(sum(est.values()), 4)
    with session_scope() as db:
        cfg = budget.get_system(db, "live")
        spent = budget.spent_today(db, "live")
    _echo({"units": count, "specs": specs, "estimate_usd_per_unit": {k: (round(v, 4) if v is not None else "unknown price") for k, v in est.items()}, "estimate_usd_total": None if per_unit is None else round(per_unit * count, 4),
           "note": "estimate only; actual usage is recorded per request. Unknown prices block execution instead of counting as zero.",
           "paid_enabled": cfg["paid_enabled"], "daily_budget_usd": cfg["daily_budget_usd"], "per_job_cap_usd": cfg["per_job_cap_usd"], "spent_today": spent})  # fmt: skip


@app.command()
def generate(mode: str, part: str, skill: str = "explicit_detail", band: int = 3, count: int = 1, topic: str = "", realm: str = "live", pool: str = "practice",
             run: bool = typer.Option(True, help="process the jobs now in this process (otherwise the worker picks them up)"),
             send: bool = typer.Option(True, help="when LQ_SERVER_URL is set, send READY units to that server (failures stay in the outbox)")) -> None:  # fmt: skip
    """Queue generation jobs (content -> blind review -> audio -> audio check -> READY)."""
    from lq.jobs import queue, worker
    from lq.services import admin

    ids = []
    with session_scope() as s:
        for spec in _specs(
            mode, part, skill, band, count, topic, pool, "demo-generated" if realm == "demo" else "generated"
        ):
            job, _ = queue.enqueue(s, realm, "generate", spec)
            ids.append(job.id)
    if run:
        worker.drain("cli")
    with session_scope() as s:
        jobs = [admin.job_dto(s, s.get(m.GenerationJob, i)) for i in ids]
    _echo(
        [
            {
                k: j[k]
                for k in (
                    "id",
                    "status",
                    "blocked_reason",
                    "blocked_label",
                    "stop_detail",
                    "last_completed_step",
                    "unit_id",
                    "unit_status",
                    "cost",
                )
            }
            for j in jobs
        ]
    )
    if send and get_settings().server_url:
        for j in jobs:
            if j["unit_status"] == "READY":
                d = _build_bundle(j["unit_id"], realm, None)
                _send_or_queue(d, realm)


@app.command()
def review(unit: str, realm: str = "live") -> None:
    """Run the blind review (and whatever follows) for a unit that is waiting."""
    _requeue(unit, realm)


@app.command()
def render(unit: str, realm: str = "live") -> None:
    """(Re)run audio synthesis / rendering / checks for a unit. Finished clips come from the cache."""
    _requeue(unit, realm, to_audio=True)


def _requeue(ref: str, realm: str, to_audio: bool = False) -> None:
    from lq.jobs import queue, worker
    from lq.services import admin

    with session_scope() as s:
        u = _find_unit(s, ref, realm)
        if u.status == "READY":
            typer.echo("unit is READY already (READY versions are immutable; publish a new revision to change audio)")
            return
        if to_audio and u.status in ("AUDIO_GENERATING", "AUDIO_QA", "FAILED"):
            u.status = "AUDIO_PENDING"
        job = (
            s.execute(
                select(m.GenerationJob)
                .where(m.GenerationJob.unit_id == u.id)
                .order_by(m.GenerationJob.created_at.desc())
            )
            .scalars()
            .first()
        )
        if job is None:
            job, _ = queue.enqueue(
                s,
                realm,
                "process_unit",
                {"origin": "cli", "mode": u.mode, "part": u.part},
                dedupe_key=f"process:{u.id}",
                unit_id=u.id,
            )
        else:
            queue.manual_retry(s, job.id)
        jid = job.id
    worker.drain("cli")
    with session_scope() as s:
        _echo(admin.job_dto(s, s.get(m.GenerationJob, jid)))


@app.command()
def approve(unit: str, target: str = typer.Option(..., help="content | audio"), reviewer: str = typer.Option(..., help="who checked it"),
            as_author: bool = typer.Option(False, help="record content approval as author_reviewed instead of manual_reviewed"), note: str = "", realm: str = "live") -> None:  # fmt: skip
    """Record an explicit human approval for the unit's current content hash (or its rendered audio)."""
    from lq.jobs import worker
    from lq.services import importer

    if target not in ("content", "audio"):
        raise typer.BadParameter("target must be content or audio")
    with session_scope() as s:
        u = _find_unit(s, unit, realm)
        rec = importer.manual_approve(
            s,
            u,
            target=target,
            reviewer=reviewer,
            method="author_reviewed" if as_author and target == "content" else None,
            note=note,
        )
        typer.echo(f"recorded {rec.method} for {u.content_id}@{u.revision} hash {u.content_hash[:12]}")
    worker.drain("cli")


def _build_bundle(unit_ref: str, realm: str, out: Path | None) -> Path:
    """Write manifest.json + per-clip audio for one unit. Review results are NOT exported as approvals."""
    from lq.audio.pcm import decode_to_pcm, wav_bytes
    from lq.domain.content import sha256_hex
    from lq.storage import get_storage

    st = get_storage()
    with session_scope() as s:
        u = _find_unit(s, unit_ref, realm)
        job = (
            s.execute(
                select(m.GenerationJob)
                .where(m.GenerationJob.unit_id == u.id)
                .order_by(m.GenerationJob.created_at.desc())
            )
            .scalars()
            .first()
        )
        clip_keys = dict((job.state or {}).get("clip_keys") or {}) if job else {}
        content, cid, rev = dict(u.content), u.content_id, u.revision
    out = out or (get_settings().outbox_dir / f"{cid}-r{rev}-{new_id()[:6]}")
    out.mkdir(parents=True, exist_ok=True)
    files, audio = {}, {}
    for clip, key in sorted(clip_keys.items()):
        name = clip.replace(":", "_") + ".wav"
        data = wav_bytes(decode_to_pcm(st.get(key)))
        (out / name).write_bytes(data)
        files[name] = {"sha256": sha256_hex(data), "bytes": len(data)}
        audio[clip] = name
    manifest = {
        "schema_version": 2,
        "source": content["source"],
        "units": [{**content, "audio": audio}],
        "files": files,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


@app.command()
def build(
    unit: str,
    out: Path = typer.Option(None, help="output directory (default: a new outbox entry)"),
    realm: str = "live",
) -> None:
    """Export a unit as an import bundle: manifest.json plus one audio file per clip."""
    d = _build_bundle(unit, realm, out)
    typer.echo(f"bundle written to {d}")


def _post_bundle(d: Path, realm: str) -> dict:
    import httpx

    s = get_settings()
    if not s.server_url or not s.import_token:
        raise RuntimeError("LQ_SERVER_URL and LQ_IMPORT_TOKEN must be set to send material")
    manifest = (d / "manifest.json").read_bytes()
    names = list(json.loads(manifest).get("files", {}))
    handles = [open(d / n, "rb") for n in names]  # noqa: SIM115
    try:
        files = [("manifest", ("manifest.json", manifest, "application/json"))] + [
            ("files", (n, h, "application/octet-stream")) for n, h in zip(names, handles, strict=True)
        ]
        r = httpx.post(
            f"{s.server_url.rstrip('/')}/api/import",
            headers={"Authorization": f"Bearer {s.import_token}"},
            data={"realm": realm},
            files=files,
            timeout=300,
        )
    finally:
        for h in handles:
            h.close()
    if r.status_code != 200:
        raise RuntimeError(f"server answered HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


def _send_or_queue(d: Path, realm: str) -> bool:
    meta_p = d / "outbox.json"
    meta = (
        json.loads(meta_p.read_text())
        if meta_p.exists()
        else {"attempts": 0, "realm": realm, "created_at": datetime.now(UTC).isoformat()}
    )
    try:
        rep = _post_bundle(d, meta["realm"])
    except Exception as e:  # noqa: BLE001 - any failure keeps the bundle for a later resend
        meta.update(
            attempts=meta["attempts"] + 1, last_error=str(e)[:500], last_attempt_at=datetime.now(UTC).isoformat()
        )
        outbox = get_settings().outbox_dir
        if outbox not in d.parents:
            target = outbox / d.name
            outbox.mkdir(parents=True, exist_ok=True)
            shutil.copytree(d, target, dirs_exist_ok=True)
            d = target
        (d / "outbox.json").write_text(json.dumps(meta, indent=1))
        typer.echo(f"send failed, kept in outbox: {d} ({meta['last_error']})", err=True)
        return False
    if rep.get("status") in ("accepted", "duplicate"):
        typer.echo(f"sent {d.name}: {rep['status']} {[(u['unit'], u['status']) for u in rep.get('units', [])]}")
        if get_settings().outbox_dir in d.parents:
            shutil.rmtree(d)
        return True
    meta.update(
        attempts=meta["attempts"] + 1,
        last_error=f"server refused: {json.dumps(rep, ensure_ascii=False)[:500]}",
        refused=True,
    )
    meta_p.write_text(json.dumps(meta, indent=1))
    typer.echo(f"server refused {d.name}: {rep.get('status')} (kept for inspection; fix and resend)", err=True)
    return False


@app.command("import")
def import_cmd(
    manifest: Path,
    files: list[Path] = typer.Argument(None),
    realm: str = "live",
    remote: bool = typer.Option(False, help="send to LQ_SERVER_URL instead of importing into the local database"),
) -> None:
    """Import a manifest / Unit JSON (schema 2, or the earlier radio generator's schema 1) with its audio."""
    from lq.jobs import worker
    from lq.services import importer

    if remote:
        d = manifest.parent
        if manifest.name != "manifest.json":
            d = get_settings().outbox_dir / f"import-{new_id()[:8]}"
            d.mkdir(parents=True)
            shutil.copy(manifest, d / "manifest.json")
            for f in files or []:
                shutil.copy(f, d / f.name)
        raise typer.Exit(0 if _send_or_queue(d, realm) else 1)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    paths = {f.name: f for f in (files or [])}
    for name in data.get("files", {}):  # files next to the manifest are picked up by plain name only
        cand = manifest.parent / Path(name).name
        if name not in paths and cand.is_file():
            paths[name] = cand
    with session_scope() as s:
        rep = importer.import_manifest(s, realm, data, {n: p.read_bytes() for n, p in paths.items()}, actor="cli")
    _echo(rep)
    worker.drain("cli")
    raise typer.Exit(0 if rep["status"] in ("accepted", "duplicate") else 1)


@outbox_app.command("list")
def outbox_list() -> None:
    root = get_settings().outbox_dir
    items = []
    for d in sorted(root.glob("*")) if root.exists() else []:
        meta = json.loads((d / "outbox.json").read_text()) if (d / "outbox.json").exists() else {}
        items.append(
            {"name": d.name, **{k: meta.get(k) for k in ("attempts", "last_error", "last_attempt_at", "refused")}}
        )
    _echo(items)


@outbox_app.command("flush")
def outbox_flush() -> None:
    """Resend everything waiting in the outbox."""
    root = get_settings().outbox_dir
    ok = fail = 0
    for d in sorted(root.glob("*")) if root.exists() else []:
        if not (d / "manifest.json").exists():
            continue
        meta = json.loads((d / "outbox.json").read_text()) if (d / "outbox.json").exists() else {}
        if _send_or_queue(d, meta.get("realm", "live")):
            ok += 1
        else:
            fail += 1
    typer.echo(f"sent {ok}, still waiting {fail}")
    raise typer.Exit(1 if fail else 0)


@app.command()
def worker(once: bool = typer.Option(False, help="process what is queued, then exit")) -> None:
    """Run the generation worker."""
    from lq.jobs import worker as w

    if once:
        _echo(w.drain(w.worker_name()))
    else:
        w.run_forever()


@app.command()
def gc(min_age_sec: int = 3600) -> None:
    """Delete stored audio objects that no asset references (left by interrupted uploads)."""
    from lq.services import content

    with session_scope() as s:
        removed = content.gc_orphan_objects(s, min_age_sec)
    typer.echo(f"removed {len(removed)} orphan object(s)")


@app.command()
def openapi(out: Path = typer.Option(..., help="where to write openapi.json")) -> None:
    """Write the OpenAPI document (the contract the web app's types are generated from)."""
    from lq.api.app import app as api

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(api.openapi(), ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    typer.echo(f"wrote {out}")


@app.command()
def schema(out: Path = typer.Option(..., help="where to write the Unit JSON Schema")) -> None:
    from lq.domain.content import unit_json_schema

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(unit_json_schema(), ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    typer.echo(f"wrote {out}")


@app.command("export-fixtures")
def export_fixtures(out: Path = typer.Option(..., help="directory for sample Unit JSON files")) -> None:
    """Write the bundled examples as schema-2 JSON (import samples)."""
    from lq.services.seed import load_fixture_units

    out.mkdir(parents=True, exist_ok=True)
    units = load_fixture_units()
    (out / "manifest.sample.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "source": "fixture",
                "units": [u.model_dump(mode="json") for u in units],
                "files": {},
            },
            ensure_ascii=False,
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
    typer.echo(f"wrote {len(units)} units to {out / 'manifest.sample.json'}")


def main() -> None:
    app()


if __name__ == "__main__":
    sys.exit(main())
