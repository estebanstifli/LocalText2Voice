from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Any

from app.core.audiobook_store import AudiobookStore, StoredAudiobook
from app.server.job_manager import LocalServerJobManager, ServerJob


DEFAULT_PAGE_SIZE_CHARS = 12_000
MAX_PAGE_SIZE_CHARS = 50_000
MAX_PAGE_COUNT = 10
MAX_READ_ALL_CHARS = 200_000
MAX_PAGED_RESPONSE_CHARS = 200_000
MAX_SEARCH_RESULTS = 100


@dataclass(frozen=True)
class JobProjectSource:
    job: ServerJob
    project: StoredAudiobook


class JobSourceEditor:
    def __init__(
        self,
        job_manager: LocalServerJobManager,
        audiobook_store: AudiobookStore | None = None,
    ) -> None:
        self.job_manager = job_manager
        self.audiobook_store = audiobook_store or AudiobookStore()

    def read(
        self,
        job_id: str,
        page: int = 1,
        page_size_chars: int = DEFAULT_PAGE_SIZE_CHARS,
        page_count: int = 1,
        read_all: bool = False,
    ) -> dict[str, Any]:
        source = self._resolve(job_id)
        text = source.project.source_text
        page_size = self._page_size(page_size_chars)
        total_pages = max(1, math.ceil(len(text) / page_size))
        if read_all:
            if len(text) > MAX_READ_ALL_CHARS:
                raise ValueError(
                    f"Source has {len(text)} characters, above the read_all limit "
                    f"of {MAX_READ_ALL_CHARS}. Read it in pages instead."
                )
            start = 0
            end = len(text)
            first_page = 1
            returned_pages = total_pages
        else:
            first_page = int(page)
            returned_pages = int(page_count)
            if first_page < 1 or first_page > total_pages:
                raise ValueError(
                    f"page must be between 1 and {total_pages}; received {first_page}."
                )
            if returned_pages < 1 or returned_pages > MAX_PAGE_COUNT:
                raise ValueError(
                    f"page_count must be between 1 and {MAX_PAGE_COUNT}."
                )
            if page_size * returned_pages > MAX_PAGED_RESPONSE_CHARS:
                raise ValueError(
                    "The requested pages exceed the combined response limit of "
                    f"{MAX_PAGED_RESPONSE_CHARS} characters. Request fewer pages."
                )
            start = (first_page - 1) * page_size
            end = min(len(text), start + page_size * returned_pages)
            returned_pages = max(1, math.ceil((end - start) / page_size))
        last_page = min(total_pages, first_page + returned_pages - 1)
        return {
            **self._source_metadata(source, text),
            "content": text[start:end],
            "read_all": bool(read_all),
            "page": first_page,
            "last_page": last_page,
            "page_count": returned_pages,
            "page_size_chars": page_size,
            "total_pages": total_pages,
            "start_offset": start,
            "end_offset": end,
            "has_previous": start > 0,
            "has_more": end < len(text),
            "next_page": last_page + 1 if end < len(text) else None,
            "offset_unit": "unicode_code_points",
        }

    def write(
        self,
        job_id: str,
        text: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        source = self._resolve(job_id)
        return self._save(source, str(text), expected_sha256, "write")

    def search(
        self,
        job_id: str,
        query: str,
        regex: bool = False,
        case_sensitive: bool = False,
        result_offset: int = 0,
        max_results: int = 25,
    ) -> dict[str, Any]:
        source = self._resolve(job_id)
        text = source.project.source_text
        pattern = self._compile_pattern(query, regex, case_sensitive)
        offset = max(0, int(result_offset))
        limit = max(1, min(MAX_SEARCH_RESULTS, int(max_results)))
        matches: list[dict[str, Any]] = []
        skipped = 0
        has_more = False
        for match in pattern.finditer(text):
            if skipped < offset:
                skipped += 1
                continue
            if len(matches) >= limit:
                has_more = True
                break
            start, end = match.span()
            snippet_start = max(0, start - 100)
            snippet_end = min(len(text), end + 100)
            matches.append(
                {
                    "start_offset": start,
                    "end_offset": end,
                    "line": text.count("\n", 0, start) + 1,
                    "match": match.group(0),
                    "snippet": text[snippet_start:snippet_end],
                    "snippet_start_offset": snippet_start,
                }
            )
        return {
            **self._source_metadata(source, text),
            "query": query,
            "regex": bool(regex),
            "case_sensitive": bool(case_sensitive),
            "result_offset": offset,
            "results": matches,
            "returned_results": len(matches),
            "has_more": has_more,
            "next_result_offset": offset + len(matches) if has_more else None,
            "offset_unit": "unicode_code_points",
        }

    def edit(
        self,
        job_id: str,
        operation: str,
        start_offset: int,
        end_offset: int | None = None,
        text: str = "",
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        source = self._resolve(job_id)
        current = source.project.source_text
        start = int(start_offset)
        end = start if end_offset is None else int(end_offset)
        if start < 0 or start > len(current):
            raise ValueError(f"start_offset must be between 0 and {len(current)}.")
        operation_name = str(operation or "").strip().casefold()
        if operation_name == "insert":
            end = start
            replacement = str(text)
        elif operation_name == "replace":
            self._validate_range(start, end, len(current))
            replacement = str(text)
        elif operation_name == "delete":
            self._validate_range(start, end, len(current))
            replacement = ""
        else:
            raise ValueError("operation must be insert, replace, or delete.")
        updated = current[:start] + replacement + current[end:]
        result = self._save(source, updated, expected_sha256, operation_name)
        result["edit"] = {
            "start_offset": start,
            "old_end_offset": end,
            "new_end_offset": start + len(replacement),
            "inserted_chars": len(replacement),
            "removed_chars": end - start,
        }
        return result

    def replace_text(
        self,
        job_id: str,
        search: str,
        replacement: str,
        replace_all: bool = False,
        occurrence: int = 1,
        regex: bool = False,
        case_sensitive: bool = True,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        source = self._resolve(job_id)
        current = source.project.source_text
        pattern = self._compile_pattern(search, regex, case_sensitive)
        first_match = pattern.search(current)
        if first_match is None:
            return {
                **self._source_metadata(source, current),
                "changed": False,
                "replacements": 0,
                "render_required": False,
            }
        if first_match.start() == first_match.end():
            raise ValueError("Search expressions that match empty text are not supported.")
        replacement_count = 0
        if replace_all:
            def replace_match(match: re.Match[str]) -> str:
                nonlocal replacement_count
                if match.start() == match.end():
                    raise ValueError(
                        "Search expressions that match empty text are not supported."
                    )
                replacement_count += 1
                try:
                    return match.expand(replacement) if regex else replacement
                except re.error as exc:
                    raise ValueError(f"Invalid replacement expression: {exc}") from exc

            updated = pattern.sub(replace_match, current)
        else:
            selected_index = int(occurrence) - 1
            if selected_index < 0:
                raise ValueError("occurrence must be 1 or greater.")
            selected_match = None
            for index, match in enumerate(pattern.finditer(current)):
                if match.start() == match.end():
                    raise ValueError(
                        "Search expressions that match empty text are not supported."
                    )
                if index == selected_index:
                    selected_match = match
                    break
            if selected_match is None:
                raise ValueError(f"Occurrence {occurrence} was not found.")
            try:
                value = (
                    selected_match.expand(replacement) if regex else replacement
                )
            except re.error as exc:
                raise ValueError(f"Invalid replacement expression: {exc}") from exc
            updated = (
                current[: selected_match.start()]
                + value
                + current[selected_match.end() :]
            )
            replacement_count = 1
        result = self._save(source, updated, expected_sha256, "replace_text")
        result["replacements"] = replacement_count
        return result

    def _resolve(self, job_id: str) -> JobProjectSource:
        job = self.job_manager.get_job(str(job_id))
        if job is None:
            raise ValueError(f"Job not found: {job_id}")
        if job.audiobook_id is None:
            raise ValueError(
                f"Job {job_id} has no editable project yet (status: {job.status})."
            )
        project = self.audiobook_store.get_audiobook(job.audiobook_id)
        if project is None:
            raise ValueError(
                f"Audiobook project not found for job {job_id}: {job.audiobook_id}"
            )
        return JobProjectSource(job, project)

    def _save(
        self,
        source: JobProjectSource,
        text: str,
        expected_sha256: str | None,
        operation: str,
    ) -> dict[str, Any]:
        old_text = source.project.source_text
        if text == old_text:
            return {
                **self._source_metadata(source, old_text),
                "changed": False,
                "operation": operation,
                "render_required": False,
            }
        project = self.audiobook_store.update_audiobook_source(
            source.project.id,
            text,
            expected_sha256=expected_sha256,
        )
        updated_source = JobProjectSource(source.job, project)
        return {
            **self._source_metadata(updated_source, text),
            "changed": True,
            "operation": operation,
            "previous_total_chars": len(old_text),
            "render_required": True,
            "message": (
                "Source updated. Existing audio still represents the previous "
                "source and must be rendered again."
            ),
        }

    @staticmethod
    def _source_metadata(source: JobProjectSource, text: str) -> dict[str, Any]:
        return {
            "job_id": source.job.job_id,
            "audiobook_id": source.project.id,
            "project_uuid": source.project.uuid,
            "title": source.project.title,
            "source_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "total_chars": len(text),
            "total_lines": text.count("\n") + 1 if text else 0,
            "source_path": str(source.project.project_dir / "source.txt"),
        }

    @staticmethod
    def _page_size(value: int) -> int:
        size = int(value)
        if size < 256 or size > MAX_PAGE_SIZE_CHARS:
            raise ValueError(
                f"page_size_chars must be between 256 and {MAX_PAGE_SIZE_CHARS}."
            )
        return size

    @staticmethod
    def _compile_pattern(
        query: str,
        regex: bool,
        case_sensitive: bool,
    ) -> re.Pattern[str]:
        value = str(query)
        if not value:
            raise ValueError("Search text cannot be empty.")
        if len(value) > 2_000:
            raise ValueError("Search text cannot exceed 2000 characters.")
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            return re.compile(value if regex else re.escape(value), flags)
        except re.error as exc:
            raise ValueError(f"Invalid regular expression: {exc}") from exc

    @staticmethod
    def _validate_range(start: int, end: int, total: int) -> None:
        if end < start or end > total:
            raise ValueError(
                f"end_offset must be between start_offset ({start}) and {total}."
            )
