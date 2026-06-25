from dataclasses import dataclass

from app import models


@dataclass(frozen=True)
class ResolvedLocation:
    city: str
    region: str | None
    query: str
    precision: str
    district: str | None = None
    address: str | None = None
    landmark: str | None = None


class LocationResolver:
    def resolve(self, *, source: models.Source, classification: models.Classification) -> ResolvedLocation:
        location_text = (classification.location_text or "").strip(" .,!?:;")
        precision = classification.location_precision or "city"

        if not location_text:
            return ResolvedLocation(
                city=source.city,
                region=source.region,
                query=self._join(source.region, source.city),
                precision="city",
            )

        if precision == "city":
            return ResolvedLocation(
                city=location_text,
                region=None,
                query=location_text,
                precision="city",
            )

        query = self._join(source.region, source.city, location_text)
        district = location_text if precision == "district" else None
        address = location_text if precision in {"street", "address"} else None
        landmark = location_text if precision == "landmark" else None

        return ResolvedLocation(
            city=source.city,
            region=source.region,
            query=query,
            precision=precision,
            district=district,
            address=address,
            landmark=landmark,
        )

    def _join(self, *parts: str | None) -> str:
        return ", ".join(part.strip() for part in parts if part and part.strip())
