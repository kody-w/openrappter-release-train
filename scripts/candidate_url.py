"""Closed parser for openrappter-candidate-url/v1."""
from __future__ import annotations
import base64
import re
from urllib.parse import urlsplit

HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

class CandidateUrlError(ValueError):
    pass

def candidate_id_for_tag(tag: str) -> str:
    if not re.fullmatch(r"v[0-9][0-9A-Za-z.+-]*", tag) or not tag.isascii():
        raise CandidateUrlError("tag is not closed ASCII release-tag syntax")
    return "tag-" + base64.urlsafe_b64encode(tag.encode()).decode().rstrip("=")

def validate_candidate_id(value: str) -> str:
    if not isinstance(value, str) or not value.isascii() or not ID.fullmatch(value) or value in {".", ".."} or "%" in value:
        raise CandidateUrlError("candidate id is not closed ASCII path-component syntax")
    return value

def parse_candidate_url(url: str) -> dict:
    if (
        not isinstance(url, str)
        or not re.fullmatch(r"[\x20-\x7e]+", url)
        or not url.startswith("https://raw.githubusercontent.com/")
    ):
        raise CandidateUrlError("candidate URL origin must use the exact allowlisted spelling")
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise CandidateUrlError("candidate URL is malformed") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != "raw.githubusercontent.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise CandidateUrlError("candidate URL origin/query/credentials are not allowed")
    if not parsed.path.isascii() or "%" in parsed.path or "\\" in parsed.path:
        raise CandidateUrlError("candidate URL path must be literal ASCII without encoding")
    parts = parsed.path.removeprefix("/").split("/")
    if len(parts) != 8 or parts[:4] != ["kody-w", "openrappter", parts[2], "candidates"]:
        raise CandidateUrlError("candidate URL owner/repository/path shape is not allowlisted")
    owner, repo, ref, marker, source, kind, candidate_id, filename = parts
    if owner != "kody-w" or repo != "openrappter" or marker != "candidates":
        raise CandidateUrlError("candidate URL repository is not allowlisted")
    if not HEX40.fullmatch(ref) or not HEX40.fullmatch(source):
        raise CandidateUrlError("candidate URL ref/source commit must be lowercase 40-hex")
    if kind not in {"snapshot", "release"}:
        raise CandidateUrlError("candidate URL kind must be snapshot or release")
    validate_candidate_id(candidate_id)
    if not filename.endswith(".tar.gz") or not HEX64.fullmatch(filename[:-7]):
        raise CandidateUrlError("candidate URL bundle filename must be 64-hex SHA-256")
    return {
        "ref": ref,
        "source_commit": source,
        "kind": kind,
        "candidate_id": candidate_id,
        "sha256": filename[:-7],
    }

def build_candidate_url(ref: str, source: str, kind: str, candidate_id: str, sha256: str) -> str:
    if not HEX40.fullmatch(ref) or not HEX40.fullmatch(source) or kind not in {"snapshot", "release"} or not HEX64.fullmatch(sha256):
        raise CandidateUrlError("candidate URL identity is malformed")
    validate_candidate_id(candidate_id)
    return f"https://raw.githubusercontent.com/kody-w/openrappter/{ref}/candidates/{source}/{kind}/{candidate_id}/{sha256}.tar.gz"
