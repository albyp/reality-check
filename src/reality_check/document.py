"""Document details and branding for the report: logos, client, document number, sign-off, appendix mode.

The same fields as CounterCheck's DocumentInfo, so the Plumb tools fill their title blocks alike (no accent
colour: the house style is ink only). Every field is optional; a report with none set still renders, titled
from the project. Company details (company, author, logo, confidentiality, sign-off) are saved as defaults in
settings.json; the rest belongs to one project and is saved in the session.
"""

from __future__ import annotations

import base64
import mimetypes
from dataclasses import asdict, dataclass, field, fields
from datetime import date
from pathlib import Path

LOGO_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml",
              ".gif": "image/gif", ".webp": "image/webp"}
MAX_LOGO_BYTES = 2_000_000
SIGN_ROLES = ("Prepared by", "Checked by", "Approved by")
COMPANY_FIELDS = ("company", "author", "logo", "confidentiality", "footer", "signatories")


def logo_data_url(path: str | Path) -> str:
    """Embed a logo file so the report stays one self-contained file."""
    p = Path(path)
    mime = LOGO_TYPES.get(p.suffix.lower()) or mimetypes.guess_type(p.name)[0]
    if mime not in LOGO_TYPES.values():
        raise ValueError(f"{p.name}: logos must be PNG, JPG, SVG, GIF or WebP")
    data = p.read_bytes()
    if len(data) > MAX_LOGO_BYTES:
        raise ValueError(f"{p.name}: logo is {len(data) / 1e6:.1f} MB; the limit is {MAX_LOGO_BYTES / 1e6:g} MB")
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


@dataclass
class Signatory:
    role: str  # "Prepared by", "Checked by", "Approved by"
    name: str = ""
    position: str = ""


@dataclass
class DocumentInfo:
    subtitle: str = ""
    project: str = ""
    site: str = ""
    client: str = ""
    company: str = ""  # the surveyor's company
    author: str = ""
    job_number: str = ""
    document_number: str = ""
    revision: str = ""
    date: str = ""  # ISO date; blank = today
    confidentiality: str = ""  # e.g. "Commercial in confidence"
    footer: str = ""  # extra title strip text
    logo: str = ""  # path to the company logo
    client_logo: str = ""  # path to the client logo
    appendix: str = ""  # e.g. "B": render as Appendix B of a larger report
    appendix_title: str = ""  # blank = the project title
    signatories: list[Signatory] = field(default_factory=list)

    @property
    def issued(self) -> str:
        return self.date or date.today().isoformat()

    @property
    def is_appendix(self) -> bool:
        return bool(self.appendix.strip())

    def heading_number(self, n: int) -> str:
        """Section numbers: "B1", "B2" in an appendix, "1", "2" otherwise."""
        return f"{self.appendix.strip()}{n}" if self.is_appendix else str(n)

    def named_signatories(self) -> list[Signatory]:
        """Rows for the sign-off table: those with a name or position filled in."""
        return [s for s in self.signatories if s.name.strip() or s.position.strip()]

    def company_defaults(self) -> DocumentInfo:
        """Only the company details, for settings.json; project details stay with the session."""
        return DocumentInfo.from_dict({k: v for k, v in self.to_dict().items() if k in COMPANY_FIELDS})

    def copy(self) -> DocumentInfo:
        return DocumentInfo.from_dict(self.to_dict())

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> DocumentInfo:
        """Unknown keys are ignored, so a file from a newer version still opens."""
        known = {f.name for f in fields(cls)}
        d = {k: v for k, v in (d or {}).items() if k in known}
        d["signatories"] = [Signatory(**{k: v for k, v in s.items() if k in ("role", "name", "position")})
                            for s in d.get("signatories", [])]
        return cls(**d)
