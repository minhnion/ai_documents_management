from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class TextBlock:
    page_num: int        
    text: str           
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class TocNode:
    level: int
    title: str
    page: int            # 1-based page number
    target_y: float      # Y-coordinate of heading anchor on the page (0 = top)
    children: List["TocNode"] = field(default_factory=list)


@dataclass
class ChunkData:
    title: str
    content: str         # full text, paragraphs separated by \n\n
    start_page: int      # 1-based
    end_page: int        # 1-based (inclusive)
    start_y: float       # Y-coordinate of chunk start on start_page
    end_y: float         # Y-coordinate of chunk end on end_page (inf = page bottom)


@dataclass
class ChapterMeta:
    title: str
    page_start: int
    page_end: int
    start_y: float
    end_y: float
    content: str       


@dataclass
class DocumentMetadata:
    title: Optional[str]
    publisher: Optional[str]
    author: Optional[str]
    subject: Optional[str]
    keywords: Optional[str]
    decision_number: Optional[str]   # WHO report number, decree number, etc.
    specialty: Optional[str]        
    date: Optional[str]              
    isbn_electronic: Optional[str]
    isbn_print: Optional[str]
    issn: Optional[str]
    total_pages: int
    source_file: str
    chapters: List[ChapterMeta] = field(default_factory=list)
