"""Import of existing material: manifest JSON (+ audio files). Shared by the admin UI, the import API
and the generator CLI. Idempotent on (source, content_id, revision); nothing is fetched from URLs or
read from server paths - audio arrives only as uploaded bytes that match the manifest's hashes."""

from __future__ import annotations

import re

from pydantic import ValidationError

from lq.audio.pcm import AudioError, decode_to_pcm
from lq.audio.render import plan_clips
from lq.db import models as m
from lq.domain.content import SKILLS, UnitContent, canonical_json, sha256_hex
from lq.domain.profiles import current_profile
from lq.generator.authoring import lint_unit, unit_scoring_groups
from lq.jobs import queue
from lq.services import content as C
from lq.storage import get_storage

MAX_FILE_BYTES = 60 * 1024 * 1024
MAX_FILES = 400
_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,150}\.(mp3|wav|m4a|ogg|flac)$")
_CLIP_ID = re.compile(r"^(utt|prompt|choices|answer|explain):[A-Za-z0-9_.\-]{1,64}$|^(pre|figdesc|radio_complete)$")


def adapt_legacy_v1(manifest: dict) -> tuple[list[dict], list[dict]]:
    """schema_version 1 = the earlier radio generator's episode.json:
    {schema_version: 1, episode_id, title, mode?, part?, audio: "<complete mp3>", items: [{id, script: [{speaker, text}],
      question, choices: [..], answer: <1-based index>, explanation_ja, evidence_line?: <1-based script line>}]}
    Items with full structure become units (still needing their own stimulus audio). Whatever cannot be
    mapped is reported; answers or evidence are never invented. The complete MP3 becomes one radio-only unit."""
    units, notes = [], []
    mode = manifest.get("mode", "common_test")
    eid = re.sub(r"[^A-Za-z0-9_.\-]", "-", str(manifest.get("episode_id", "episode")))[:80]
    try:
        profile = current_profile(mode)
    except KeyError:
        return [], [{"item": eid, "status": "rejected", "reason": f"unknown mode {mode!r}"}]
    for k in set(manifest) - {
        "schema_version",
        "episode_id",
        "title",
        "mode",
        "part",
        "audio",
        "items",
        "files",
        "source",
    }:
        notes.append({"item": eid, "status": "ignored_field", "reason": f"field {k!r} has no counterpart in schema 2"})
    if manifest.get("audio"):
        units.append({
            "unit": {
                "schema_version": 2, "source": manifest.get("source", "legacy-radio"), "content_id": f"{eid}-radio", "revision": 1, "mode": mode, "profile_version": profile["id"],
                "part": manifest.get("part", profile["parts"][0]["part"]), "template_type": "legacy_radio_episode", "title": str(manifest.get("title", eid))[:120], "topic": "legacy",
                "skill_tags": ["main_idea"], "primary_skill": "main_idea", "provisional_difficulty_band": 3, "family_id": f"legacy:{eid}",
                "presentation_profile": {"kind": "audio_arranged", "choices_read_aloud": True, "figure_verbalized": False, "notes": "answer-included complete audio from the earlier generator"},
                "speakers": [{"id": "narrator", "role": "narrator", "label": "Narrator", "voice_hint": "neutral"}],
                "segments": [{"id": "s1", "label": "", "plays": 1, "utterances": [{"id": "u1", "speaker_id": "narrator", "lang": "en", "text": "(complete episode audio; no separate script)"}]}],
                "questions": [], "legacy_radio_only": True, "radio": {"supported": True, "reason": "", "figure_description_en": ""},
            },
            "audio": {"radio_complete": manifest["audio"]},
        })  # fmt: skip
        notes.append(
            {
                "item": f"{eid}-radio",
                "status": "radio_only",
                "reason": "only answer-included audio exists: usable for radio, never offered for answering",
            }
        )
    for it in manifest.get("items", []):
        iid = re.sub(r"[^A-Za-z0-9_.\-]", "-", str(it.get("id", "item")))[:40]
        script, choices, ans, ev = (
            it.get("script") or [],
            it.get("choices") or [],
            it.get("answer"),
            it.get("evidence_line"),
        )
        missing = [k for k, ok in (("script", script), ("question", it.get("question")), ("choices", len(choices) >= 2), ("answer", isinstance(ans, int) and 1 <= ans <= len(choices)),
                                   ("explanation_ja", it.get("explanation_ja")), ("evidence_line", isinstance(ev, int) and 1 <= ev <= len(script))) if not ok]  # fmt: skip
        if missing:
            notes.append(
                {
                    "item": f"{eid}-{iid}",
                    "status": "not_converted",
                    "reason": f"missing or unusable: {', '.join(missing)} (not filled in automatically)",
                }
            )
            continue
        speakers = []
        for line in script:
            sid = re.sub(r"[^A-Za-z0-9_]", "_", str(line.get("speaker", "sp1")))[:20] or "sp1"
            if sid not in [s["id"] for s in speakers]:
                speakers.append(
                    {"id": sid, "role": "speaker", "label": str(line.get("speaker", sid))[:12], "voice_hint": "neutral"}
                )
        speakers.insert(0, {"id": "narrator", "role": "narrator", "label": "Narrator", "voice_hint": "neutral"})
        skill = it.get("skill") if it.get("skill") in SKILLS else "explicit_detail"
        units.append({
            "unit": {
                "schema_version": 2, "source": manifest.get("source", "legacy-radio"), "content_id": f"{eid}-{iid}", "revision": 1, "mode": mode, "profile_version": profile["id"],
                "part": it.get("part", manifest.get("part", "3" if mode == "common_test" else profile["parts"][0]["part"])), "template_type": it.get("template_type", "short_dialogue" if mode == "common_test" else profile["parts"][0]["template_type"]),
                "title": f"{str(manifest.get('title', eid))[:90]} / {iid}", "topic": "legacy", "skill_tags": [skill], "primary_skill": skill, "provisional_difficulty_band": int(it.get("band", 3)),
                "family_id": f"legacy:{eid}:{iid}", "speakers": speakers,
                "segments": [{"id": "s1", "label": "", "plays": 1, "utterances": [{"id": f"u{i + 1}", "speaker_id": re.sub(r"[^A-Za-z0-9_]", "_", str(line.get("speaker", "sp1")))[:20] or "sp1", "lang": "en", "text": line["text"]} for i, line in enumerate(script)]}],
                "questions": [{"id": "q1", "slot": 1, "prompt_en": it["question"], "prompt_medium": "screen", "prompt_timing": "before", "type": "single_choice",
                               "choices": [{"id": f"c{i + 1}", "text": c} for i, c in enumerate(choices)], "answer": {"type": "single_choice", "choice_id": f"c{ans}"}, "skill_tags": [skill],
                               "evidence": [{"utterance_id": f"u{ev}"}], "feedback": {"answer_en": choices[ans - 1], "explanation_ja": it["explanation_ja"], "distractors": []}}],
                "scoring_groups": unit_scoring_groups(profile, it.get("part", manifest.get("part", "3" if mode == "common_test" else profile["parts"][0]["part"])), ["q1"]),
            },
            "audio": {},
        })  # fmt: skip
        notes.append(
            {
                "item": f"{eid}-{iid}",
                "status": "converted",
                "reason": "needs its own stimulus audio and a content review before it can be answered",
            }
        )
    return units, notes


def _validate_files(manifest: dict, files: dict[str, bytes]) -> list[str]:
    errs = []
    declared = manifest.get("files") or {}
    if len(files) > MAX_FILES:
        errs.append(f"too many files (max {MAX_FILES})")
    for name, data in files.items():
        if not _FILENAME.match(name) or "/" in name or "\\" in name or ".." in name:
            errs.append(f"{name!r}: file name not allowed (plain names with mp3/wav/m4a/ogg/flac only)")
            continue
        if len(data) > MAX_FILE_BYTES:
            errs.append(f"{name}: larger than {MAX_FILE_BYTES // 1024 // 1024} MB")
        if name not in declared:
            errs.append(f"{name}: uploaded but not listed in manifest.files")
        elif declared[name].get("sha256") != sha256_hex(data):
            errs.append(f"{name}: sha256 does not match the manifest")
    return errs


def import_manifest(
    session, realm: str, manifest: dict, files: dict[str, bytes] | None = None, *, actor: str = "import"
) -> dict:
    files = files or {}
    report = {"status": "accepted", "units": [], "notes": [], "errors": []}
    if not isinstance(manifest, dict):
        return {**report, "status": "rejected", "errors": ["manifest must be a JSON object"]}
    mhash = sha256_hex(canonical_json(manifest))
    version = manifest.get("schema_version")
    if version == 1:
        entries, notes = adapt_legacy_v1(manifest)
        report["notes"] += notes
    elif version == 2:
        if "units" in manifest:
            entries = [
                {
                    "unit": {k: v for k, v in u.items() if k not in ("audio", "review")},
                    "audio": u.get("audio") or {},
                    "claim": u.get("review"),
                }
                for u in manifest["units"]
            ]
        else:  # a bare Unit JSON
            entries = [
                {
                    "unit": {k: v for k, v in manifest.items() if k not in ("audio", "review", "files")},
                    "audio": manifest.get("audio") or {},
                    "claim": manifest.get("review"),
                }
            ]
    else:
        return {
            **report,
            "status": "rejected",
            "errors": [f"unsupported schema_version {version!r} (supported: 1 via adapter, 2)"],
        }
    ferrs = _validate_files(manifest, files)
    if ferrs:
        report.update(status="rejected", errors=ferrs)
        session.add(
            m.ImportBatch(
                realm=realm,
                source=str(manifest.get("source", "?"))[:64],
                manifest_hash=mhash,
                status="rejected",
                report=report,
            )
        )
        return report
    st = get_storage()
    batch = m.ImportBatch(
        realm=realm,
        source=str(manifest.get("source") or (entries[0]["unit"].get("source") if entries else "?"))[:64],
        manifest_hash=mhash,
        status="accepted",
        report={},
    )
    session.add(batch)
    session.flush()
    any_ok = False
    for e in entries:
        raw = e["unit"]
        label = f"{raw.get('source')}/{raw.get('content_id')}@{raw.get('revision', 1)}"
        try:
            content = UnitContent.model_validate(raw)
        except ValidationError as ex:
            report["units"].append(
                {
                    "unit": label,
                    "status": "rejected",
                    "errors": [f"{'.'.join(str(x) for x in er['loc'])}: {er['msg']}" for er in ex.errors()[:12]],
                }
            )
            continue
        if e.get("claim"):
            report["notes"].append(
                {
                    "item": label,
                    "status": "review_claim_ignored",
                    "reason": "a 'passed' flag inside imported JSON is not an approval; run the API review or approve explicitly",
                }
            )
        audio = e["audio"]
        aerrs = [f"audio key {k!r} is not a valid clip id" for k in audio if not _CLIP_ID.match(k)]
        aerrs += [f"audio file {v!r} for {k} was not uploaded" for k, v in audio.items() if v not in files]
        if not content.legacy_radio_only:
            aerrs += lint_unit(content, require_distractors=version != 1)
        elif "radio_complete" not in audio:
            aerrs.append("legacy_radio_only unit needs a radio_complete audio file")
        staged = {}
        if not aerrs:
            for clip, fname in audio.items():
                try:
                    decode_to_pcm(files[fname])
                except AudioError as ex:
                    aerrs.append(f"{fname}: cannot be decoded as audio ({ex})")
                    continue
                key = f"staging/{batch.id}/{sha256_hex(files[fname])}.{fname.rsplit('.', 1)[1]}"
                st.put(key, files[fname], "application/octet-stream")
                staged[clip] = key
        if aerrs:
            report["units"].append({"unit": label, "status": "rejected", "errors": aerrs})
            continue
        try:
            with session.begin_nested():
                unit, status = C.register_unit(session, realm, content, pool="practice")
        except C.ContentConflict as ex:
            report["units"].append({"unit": label, "status": "conflict", "errors": [str(ex)]})
            continue
        entry = {"unit": label, "unit_id": unit.id, "status": status, "content_hash": unit.content_hash}
        if status == "created":
            required = [] if content.legacy_radio_only else list(plan_clips(content))
            missing = [c for c in required if c not in staged]
            entry["audio"] = {
                "provided": sorted(staged),
                "missing_clips": missing,
                "note": "missing clips are synthesised if a speech provider is enabled; otherwise the job waits"
                if missing
                else "all required clips provided: speech synthesis is skipped",
            }
            job, _ = queue.enqueue(
                session,
                realm,
                "process_unit",
                {"origin": actor, "mode": content.mode, "part": content.part},
                dedupe_key=f"process:{unit.id}",
                unit_id=unit.id,
                state={"import_audio": staged},
            )
            entry["job_id"] = job.id
        any_ok = True
        report["units"].append(entry)
    if not any_ok and entries:
        report["status"] = "conflict" if any(u["status"] == "conflict" for u in report["units"]) else "rejected"
    elif all(u["status"] == "duplicate" for u in report["units"]) and report["units"]:
        report["status"] = "duplicate"
    batch.status = report["status"]
    batch.report = report
    report["batch_id"] = batch.id
    return report


def manual_approve(
    session, unit: m.Unit, *, target: str, reviewer: str, method: str | None = None, note: str = ""
) -> m.ReviewRecord:
    """Explicit human approval of the current content hash (content) or of the rendered audio (audio)."""
    if target == "content":
        method = method or "manual_reviewed"
        if method not in ("manual_reviewed", "author_reviewed"):
            raise ValueError("content approval must be manual_reviewed or author_reviewed")
        reasons = ("awaiting_content_review",)
    else:
        method = "audio_human_listened"
        reasons = ("awaiting_audio_approval",)
    rec = C.add_review_record(
        session, unit, target=target, method=method, reviewer=reviewer, result="pass", details={"note": note}
    )
    queue.release_blocked(session, unit.realm, reasons, unit_id=unit.id)
    return rec
