from datetime import date

from laglitsynth.models import _Base


class Institution(_Base):
    id: str | None = None
    display_name: str | None = None
    ror: str | None = None
    country_code: str | None = None
    type: str | None = None


class Author(_Base):
    id: str | None = None
    display_name: str
    orcid: str | None = None


class Authorship(_Base):
    author_position: str
    author: Author
    institutions: list[Institution]
    countries: list[str]
    is_corresponding: bool | None = None
    raw_affiliation_strings: list[str]


class Source(_Base):
    id: str
    display_name: str
    issn_l: str | None = None
    issn: list[str] | None = None
    type: str | None = None
    host_organization_name: str | None = None


class Location(_Base):
    is_oa: bool | None = None
    landing_page_url: str | None = None
    pdf_url: str | None = None
    source: Source | None = None
    version: str | None = None
    license: str | None = None


class OpenAccess(_Base):
    is_oa: bool | None = None
    oa_status: str | None = None
    oa_url: str | None = None


class Biblio(_Base):
    volume: str | None = None
    issue: str | None = None
    first_page: str | None = None
    last_page: str | None = None


class TopicHierarchy(_Base):
    id: str
    display_name: str


class Topic(_Base):
    id: str
    display_name: str
    score: float
    subfield: TopicHierarchy
    field: TopicHierarchy
    domain: TopicHierarchy


class Keyword(_Base):
    id: str
    display_name: str
    score: float


class Work(_Base):
    id: str
    doi: str | None = None
    title: str | None = None
    type: str | None = None
    publication_year: int | None = None
    publication_date: date | None = None
    language: str | None = None
    authorships: list[Authorship]
    biblio: Biblio
    primary_location: Location | None = None
    open_access: OpenAccess | None = None
    cited_by_count: int
    referenced_works: list[str]
    updated_date: str | None = None
    keywords: list[Keyword]
    topics: list[Topic]
    primary_topic: Topic | None = None
    abstract: str | None = None
    is_retracted: bool | None = None


class FetchMeta(_Base):
    tool: str = "laglitsynth.openalex.fetch"
    tool_version: str = "alpha"
    query: str
    fetched_at: str
    total_count: int
    records_written: int
