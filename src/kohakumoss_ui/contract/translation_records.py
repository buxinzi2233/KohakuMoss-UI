"""Pure translation snapshot and segment revision operations."""
import copy
import time

from .translation_schema import canonical, fail


def now():
    return int(time.time())


def pair(record, source, target):
    return record["sourceLanguage"] == source and record["targetLanguage"] == target


def identity(record, document):
    return (record["sourceVersion"] == document["sourceVersion"] and
            record["generation"] == document["generation"])


def snapshots(record):
    return [] if record is None else [record] + record["history"]


def exact_snapshot(record, document, source, target):
    matches = [r for r in snapshots(record) if pair(r, source, target) and identity(r, document)]
    return max(matches, key=lambda r: r["revision"]) if matches else None


def choose_snapshot(record, document, source, target):
    matches = [r for r in snapshots(record) if pair(r, source, target) and
               (identity(r, document) or r["generation"] <= document["generation"])]
    return max(matches, key=lambda r: (identity(r, document), r["generation"], r["revision"])) if matches else None


def find_segment(record, segment_id):
    return next((s for s in record["segments"] if s["segmentId"] == segment_id), None) if record else None


def max_segment_revision(record, segment_id, source, target):
    return max((s["segmentRevision"] for r in snapshots(record) if pair(r, source, target)
                for s in r["segments"] if s["segmentId"] == segment_id), default=0)


def base_record(record, document, source, target):
    if record and pair(record, source, target) and identity(record, document):
        return copy.deepcopy(record)
    history = copy.deepcopy(record["history"]) if record else []
    base = exact_snapshot(record, document, source, target)
    segments = copy.deepcopy(base["segments"]) if base else []
    if record:
        history.append({k: copy.deepcopy(v) for k, v in record.items() if k != "history"})
    result = dict(schemaVersion=1, libraryId=document["libraryId"], documentId=document["documentId"],
                  sourceHash=document["sourceHash"], sourceVersion=document["sourceVersion"],
                  generation=document["generation"], sourceLanguage=source, targetLanguage=target,
                  revision=record["revision"] if record else 0, segments=segments, history=history, updatedAt=now())
    check_size(result)
    return result


def check_size(record):
    if len(record["history"]) > 10 or len(canonical(record)) > 10 * 1024 * 1024 - 128:
        fail("STORAGE_FULL", 507)


def make_segment(segment, source, target, engine, text=None, status="translated",
                 dictionary=None, config=None, model=None, code=None):
    result = {key: segment[key] for key in ("segmentId", "sourceHash", "sourceVersion", "generation", "recordId", "pointer", "ordinal")}
    result.update(segmentRevision=0, sourceText=segment["text"], targetText=segment["text"] if text is None else text,
                  status=status, engine=engine, dictionaryVersion=dictionary, configVersion=config,
                  model=model, sourceLanguage=source, targetLanguage=target, manual=engine == "manual",
                  staleReason=None, updatedAt=now(), code=code)
    return result


def merge(record, result):
    old = find_segment(record, result["segmentId"])
    excluded = {"updatedAt", "segmentRevision", "code"}
    value = {k: v for k, v in result.items() if k != "code"}
    if old and {k: v for k, v in old.items() if k not in excluded} == {k: v for k, v in value.items() if k not in excluded}:
        return copy.deepcopy(old), False
    value["segmentRevision"] = max_segment_revision(record, value["segmentId"], value["sourceLanguage"], value["targetLanguage"]) + 1
    record["segments"] = [s for s in record["segments"] if s["segmentId"] != value["segmentId"]] + [value]
    record["updatedAt"] = now()
    check_size(record)
    return value, True


def display(segment, document, settings, dictionary_version):
    result = copy.deepcopy(segment)
    reason = None
    if not identity(result, document):
        reason = "source_changed"
    elif result["engine"] == "dictionary" and result["dictionaryVersion"] != dictionary_version:
        reason = "dictionary_changed"
    elif result["engine"] == "api" and result["configVersion"] != settings["configVersion"]:
        reason = "settings_changed"
    if reason:
        result.update(status="stale", staleReason=reason)
    result["code"] = None
    return result
