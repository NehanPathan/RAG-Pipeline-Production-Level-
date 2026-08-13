from __future__ import annotations

import json
from typing import Any, cast

from src.domain.entities.document import DocumentMetadata
from src.llm.providers.base import LLMProvider
from src.monitoring.logger import get_logger

logger = get_logger(__name__)

ENRICH_PROMPT = """\
You are a document analyst. Given the following document content, extract structured metadata.

Return ONLY valid JSON with these fields:
- summary: 2-3 sentence summary of the document
- tags: list of 3-8 relevant keyword tags (lowercase)
- domain: one of [HR, Legal, Finance, Operations, Engineering, Sales, Marketing, General]
- language: ISO 639-1 language code (e.g., "en")
- entities: list of {{name, type}} where type is one of [person, organization, date, product, location]

Document content (first 2000 chars):
{content}

JSON response:"""


class LLMMetadataEnricher:
    def __init__(self, llm_provider: LLMProvider) -> None:
        self._llm = llm_provider

    async def enrich(
        self,
        content: str,
        file_name: str,
        parsed_document: object | None = None,
    ) -> DocumentMetadata:
        # `parsed_document` is accepted and ignored: it is part of the
        # MetadataEnricher protocol so a domain enricher can use layout and
        # OCR data, and this one works from raw text alone.
        truncated = content[:2000]
        prompt = ENRICH_PROMPT.format(content=truncated)

        try:
            response = await self._llm.complete(
                prompt=prompt,
                max_tokens=512,
                temperature=0.1,
            )
            data = self._parse_json(response)
            metadata = DocumentMetadata(
                summary=data.get("summary", ""),
                tags=data.get("tags", []),
                domain=data.get("domain", "General"),
                language=data.get("language", "en"),
                entities=data.get("entities", []),
            )
            logger.info(
                "llm_enrichment_done",
                file=file_name,
                domain=metadata.domain,
                tags=metadata.tags,
            )
            return metadata
        except Exception as e:
            logger.warning("llm_enrichment_failed", file=file_name, error=str(e))
            return DocumentMetadata()

    def _parse_json(self, text: str) -> dict[str, Any]:
        text = text.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1])
        try:
            return cast(dict[str, Any], json.loads(text))
        except json.JSONDecodeError:
            # Try extracting JSON object
            start = text.find("{")
            end = text.rfind("}") + 1
            if start != -1 and end > start:
                return cast(dict[str, Any], json.loads(text[start:end]))
            return {}
