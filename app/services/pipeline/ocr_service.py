from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings
from app.core.exceptions import BadRequestException, UnprocessableEntityException

try:
    from landingai_ade import LandingAIADE
except Exception:  # pragma: no cover
    LandingAIADE = None


class LandingAIOcrService:
    async def ocr_markdown(self, pdf_path: Path) -> str:
        sdk_error: Exception | None = None
        if settings.LANDINGAI_USE_SDK:
            try:
                markdown = await self._ocr_markdown_via_sdk(pdf_path)
                if markdown is not None and markdown.strip():
                    return markdown
                sdk_error = UnprocessableEntityException(
                    "LandingAI SDK OCR returned empty markdown content."
                )
            except Exception as exc:  # noqa: BLE001
                sdk_error = exc

        try:
            return await self._ocr_markdown_via_http(pdf_path)
        except Exception as http_exc:  # noqa: BLE001
            if sdk_error is not None:
                sdk_msg = getattr(sdk_error, "detail", str(sdk_error))
                http_msg = getattr(http_exc, "detail", str(http_exc))
                raise UnprocessableEntityException(
                    f"LandingAI OCR failed via SDK and HTTP. sdk_error={sdk_msg}; http_error={http_msg}"
                ) from http_exc
            raise

    async def _ocr_markdown_via_sdk(self, pdf_path: Path) -> str | None:
        if LandingAIADE is None:
            raise BadRequestException(
                "LANDINGAI_USE_SDK=true but dependency 'landingai-ade' is missing."
            )

        def _parse_sync() -> str | None:
            client = LandingAIADE(apikey=settings.LANDINGAI_API_KEY.strip())
            result = client.parse(
                document=pdf_path,
                model=settings.LANDINGAI_MODEL_NAME.strip(),
            )
            return getattr(result, "markdown", None)

        try:
            markdown = await asyncio.to_thread(_parse_sync)
            return markdown if isinstance(markdown, str) else None
        except Exception as exc:
            raise UnprocessableEntityException(
                f"LandingAI SDK OCR failed: {exc}"
            ) from exc

    async def _ocr_markdown_via_http(self, pdf_path: Path) -> str:
        endpoint_candidates = self._resolve_landingai_endpoints(settings.LANDINGAI_API_URL.strip())
        headers = {
            "Authorization": f"Bearer {settings.LANDINGAI_API_KEY.strip()}",
            "apikey": settings.LANDINGAI_API_KEY.strip(),
        }
        data = {"model": settings.LANDINGAI_MODEL_NAME.strip()}
        timeout = httpx.Timeout(300.0, connect=60.0)
        errors: list[str] = []

        for endpoint in endpoint_candidates:
            for file_field in self._resolve_landingai_file_fields(endpoint):
                try:
                    async with httpx.AsyncClient(timeout=timeout) as client:
                        with pdf_path.open("rb") as file_obj:
                            response = await client.post(
                                endpoint,
                                headers=headers,
                                data=data,
                                files={file_field: (pdf_path.name, file_obj, "application/pdf")},
                            )
                except Exception as exc:
                    errors.append(f"{endpoint} [{file_field}]: request failed: {exc}")
                    continue

                if response.status_code in (401, 403):
                    raise UnprocessableEntityException(
                        f"LandingAI OCR unauthorized ({response.status_code}) at {endpoint}: {response.text[:500]}"
                    )
                if response.status_code >= 400:
                    errors.append(f"{endpoint} [{file_field}]: http {response.status_code}: {response.text[:300]}")
                    continue

                payload = self._safe_json(response.text)
                markdown = self._extract_markdown(payload)
                if markdown is None or not markdown.strip():
                    errors.append(f"{endpoint} [{file_field}]: response does not contain markdown")
                    continue
                return markdown

        raise UnprocessableEntityException(
            f"LandingAI OCR failed for all endpoint/file-field combinations: {' | '.join(errors)[:1500]}"
        )

    def _resolve_landingai_endpoints(self, configured_endpoint: str) -> list[str]:
        endpoint = configured_endpoint.strip().rstrip("/")
        if not endpoint:
            return []

        candidates = [endpoint]
        if endpoint.endswith("/v1/tools/document-analysis"):
            candidates.append(endpoint[: -len("/v1/tools/document-analysis")] + "/v1/ade/parse")
        elif endpoint.endswith("/v1/ade/parse"):
            candidates.append(endpoint[: -len("/v1/ade/parse")] + "/v1/tools/document-analysis")

        deduped: list[str] = []
        seen: set[str] = set()
        for item in candidates:
            normalized = item.rstrip("/")
            if normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped

    def _resolve_landingai_file_fields(self, endpoint: str) -> list[str]:
        endpoint_lower = endpoint.lower()
        if "/v1/ade/parse" in endpoint_lower:
            return ["document", "pdf", "file"]
        if "/v1/tools/document-analysis" in endpoint_lower:
            return ["pdf", "image", "document", "file"]
        return ["document", "pdf", "file", "image"]

    def _safe_json(self, raw: str) -> dict[str, Any]:
        import json

        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        raise UnprocessableEntityException("Cannot parse JSON response from LandingAI service.")

    def _extract_markdown(self, payload: Any) -> str | None:
        if isinstance(payload, dict):
            value = payload.get("markdown")
            if isinstance(value, str):
                return value
            for child in payload.values():
                found = self._extract_markdown(child)
                if found:
                    return found
        if isinstance(payload, list):
            for child in payload:
                found = self._extract_markdown(child)
                if found:
                    return found
        return None
