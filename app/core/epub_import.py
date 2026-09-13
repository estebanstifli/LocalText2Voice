"""Read DRM-free EPUB containers without extracting archives or adding dependencies.

EPUB import was proposed by Tomas-Falcon in LocalText2Voice PR #23. This reader
adapts that contribution to the current project and M4B metadata model.
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET
from zipfile import ZipFile


@dataclass
class ImportedDocument:
    text: str
    metadata: dict = field(default_factory=dict)
    cover: bytes | None = None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _target(base: str, href: str) -> tuple[str, str]:
    url = urlsplit(href)
    if url.scheme or url.netloc:
        raise ValueError("External EPUB resources are not supported.")
    path = posixpath.normpath(posixpath.join(posixpath.dirname(base), unquote(url.path))) if url.path else base
    if path.startswith(("/", "../")) or path == ".." or "\\" in path:
        raise ValueError("Invalid EPUB resource path.")
    return path, unquote(url.fragment)


class _Text(HTMLParser):
    """Preserve inline spaces and punctuation; delimit all common block elements."""
    blocks = {"p", "div", "section", "article", "li", "ul", "ol", "blockquote", "pre", "dl", "dt", "dd", "tr", "table"}
    ignored = {"head", "script", "style", "nav"}

    def __init__(self, anchors: dict[str, str]):
        super().__init__(convert_charrefs=True)
        self.anchors = anchors
        self.parts: list[str] = []
        self.lines: list[str] = []
        self.heading = False
        self.skip = 0
        self.used: set[str] = set()
        self.toc_heading_pending = False

    def flush(self):
        value = re.sub(r"\s+", " ", "".join(self.parts)).strip()
        if value:
            line = ("# " if self.heading else "") + value
            if not (self.heading and self.toc_heading_pending) and not (self.lines and self.lines[-1] == line and self.heading):
                self.lines.append(line)
            self.toc_heading_pending = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        tag = _local(tag)
        if tag in self.ignored:
            self.skip += 1
        if self.skip:
            return
        attrs = dict(attrs)
        anchor = attrs.get("id") or attrs.get("name")
        if anchor in self.anchors and anchor not in self.used:
            self.flush()
            self.lines.append("# " + self.anchors[anchor])
            self.used.add(anchor)
            self.toc_heading_pending = True
        if re.fullmatch(r"h[1-6]", tag):
            self.flush()
            self.heading = True
        elif tag in self.blocks:
            self.flush()
        elif tag in {"br", "hr", "td", "th"}:
            self.parts.append("\n" if tag == "br" else " ")

    def handle_endtag(self, tag):
        tag = _local(tag)
        if tag in self.ignored:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if re.fullmatch(r"h[1-6]", tag):
            self.flush()
            self.heading = False
        elif tag in self.blocks:
            self.flush()

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def read_epub(path: Path) -> ImportedDocument:
    with ZipFile(path) as archive:
        total = 0

        def read(name):
            nonlocal total
            info = archive.getinfo(name)
            total += info.file_size
            if info.file_size > 32 * 1024 * 1024 or total > 128 * 1024 * 1024:
                raise ValueError("EPUB text or image resources exceed the import size limit.")
            return archive.read(info)

        container = ET.fromstring(read("META-INF/container.xml"))
        roots = [e for e in container.iter() if _local(e.tag) == "rootfile"]
        if not roots:
            raise ValueError("EPUB package document is missing.")
        package_path, _ = _target("container.xml", roots[0].get("full-path", ""))
        package = ET.fromstring(read(package_path))
        manifest = {e.get("id"): e for e in package.iter() if _local(e.tag) == "item"}
        meta_element = next((e for e in package if _local(e.tag) == "metadata"), None)
        metadata = {}
        if meta_element is not None:
            fields = {"title": "title", "creator": "author", "language": "language", "publisher": "publisher", "description": "description", "date": "publication_date", "rights": "copyright"}
            for xml_name, field_name in fields.items():
                values = ["".join(e.itertext()).strip() for e in meta_element if _local(e.tag) == xml_name]
                values = [v for v in values if v]
                if values:
                    metadata[field_name] = ", ".join(values) if field_name == "author" else values[0]
            for e in meta_element:
                if _local(e.tag) == "identifier":
                    value = "".join(e.itertext()).strip()
                    scheme = next((v for k, v in e.attrib.items() if _local(k) == "scheme"), "")
                    if scheme.casefold() == "isbn" or value.casefold().startswith("urn:isbn:"):
                        metadata["isbn"] = re.sub(r"(?i)^urn:isbn:", "", value)
                        break
        if metadata.get("title"):
            metadata["title_follows_project"] = False
        metadata["chapter_mode"] = "headings"

        # Prefer EPUB 3 navigation, with EPUB 2 NCX as fallback.
        toc: dict[tuple[str, str], str] = {}
        nav_item = next((e for e in manifest.values() if "nav" in e.get("properties", "").split()), None)
        spine = next((e for e in package if _local(e.tag) == "spine"), None)
        if spine is None:
            raise ValueError("EPUB reading order is missing.")
        ncx = manifest.get(spine.get("toc"))
        if nav_item is not None:
            nav_path, _ = _target(package_path, nav_item.get("href", ""))
            nav = ET.fromstring(read(nav_path))
            for section in nav.iter():
                kind = next((v for k, v in section.attrib.items() if _local(k) == "type"), "")
                if _local(section.tag) == "nav" and "toc" in kind.split():
                    for link in section.iter():
                        if _local(link.tag) == "a" and link.get("href"):
                            label = " ".join("".join(link.itertext()).split())
                            if label:
                                toc.setdefault(_target(nav_path, link.get("href")), label)
        if not toc and ncx is not None:
            ncx_path, _ = _target(package_path, ncx.get("href", ""))
            for point in ET.fromstring(read(ncx_path)).iter():
                if _local(point.tag) != "navPoint":
                    continue
                label = next((e for e in point if _local(e.tag) == "navLabel"), None)
                content = next((e for e in point if _local(e.tag) == "content"), None)
                if label is not None and content is not None and content.get("src"):
                    toc.setdefault(_target(ncx_path, content.get("src")), " ".join("".join(label.itertext()).split()))

        cover_item = next((e for e in manifest.values() if "cover-image" in e.get("properties", "").split()), None)
        if cover_item is None and meta_element is not None:
            cover_id = next((e.get("content") for e in meta_element if e.get("name") == "cover"), None)
            cover_item = manifest.get(cover_id)
        cover = read(_target(package_path, cover_item.get("href", ""))[0]) if cover_item is not None else None

        blocks = []
        visited = set()
        for ref in spine:
            item = manifest.get(ref.get("idref"))
            if ref.get("linear", "yes") == "no" or item is None or item is nav_item:
                continue
            if item.get("media-type") not in {"application/xhtml+xml", "text/html"}:
                continue
            name, _ = _target(package_path, item.get("href", ""))
            if name in visited:
                continue
            visited.add(name)
            data = read(name)
            encoding = re.search(br'<\?xml[^>]*encoding=[\'"]([^\'"]+)', data[:200])
            html = data.decode(encoding[1].decode("ascii") if encoding else "utf-8-sig")
            parser = _Text({anchor: label for (file, anchor), label in toc.items() if file == name and anchor})
            parser.feed(html)
            parser.close()
            parser.flush()
            if parser.lines:
                title = toc.get((name, ""))
                if title:
                    if parser.lines[0].startswith("# "):
                        parser.lines[0] = "# " + title
                    else:
                        parser.lines.insert(0, "# " + title)
                blocks.append("\n\n".join(parser.lines))
        text = "\n\n".join(blocks)
        if not text.strip():
            raise ValueError("EPUB contains no readable text. Image-only or DRM-protected books are not supported.")
        return ImportedDocument(text, metadata, cover)
