"""Playwright boundary for the narrowly verified InforEstudante workflow."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urljoin, urlparse
from typing import Any

from .config import Settings
from .models import ClassSnapshot, CourseListRow


class PortalAssertionError(RuntimeError):
    """Portal structure or origin did not match the verified contract."""


class PortalAuthRequired(PortalAssertionError):
    """The saved browser state is missing or expired."""


class SubmissionUncertain(PortalAssertionError):
    """A save was attempted but authoritative confirmation was unavailable."""


@dataclass(frozen=True)
class CourseLink:
    href: str
    row: CourseListRow


def assert_list_url(url: str, settings: Settings) -> None:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != settings.expected_host
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != settings.expected_list_path
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise PortalAuthRequired("portal did not remain on the authenticated list page")


def _require_playwright() -> Any:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    except ImportError as exc:  # pragma: no cover - exercised only without runtime dependency
        raise RuntimeError("Playwright 1.63.0 is required for browser commands") from exc
    return PlaywrightTimeoutError


def _cells(row: Any) -> list[str]:
    return [str(value).strip() for value in row.locator(":scope > td").all_text_contents()]


def _direct_cell_text(cell: Any) -> str:
    """Read direct text nodes only, excluding superscript footnotes such as `1)`."""

    value = cell.evaluate(
        """element => Array.from(element.childNodes)
            .filter(node => node.nodeType === 3)
            .map(node => node.textContent || '')
            .join(' ')
            .trim()"""
    )
    return str(value).strip()


def _course_title(cells: list[str], settings: Settings) -> str:
    if len(cells) < 2 or cells[1].replace("\xa0", " ").strip() != settings.course_title:
        return ""
    return settings.course_title


def _selected_profile_classes(cell: Any, profile_alt: str) -> tuple[tuple[str, str], ...]:
    """Extract class labels from individual text nodes, excluding nested footnotes."""

    names = cell.evaluate(
        """(element, prefix) => {
            const values = [];
            const pattern = new RegExp('^' + prefix + '\\\\d+$');
            for (const current of [element, ...element.querySelectorAll('*')]) {
                for (const node of current.childNodes) {
                    if (node.nodeType !== 3) continue;
                    const value = (node.textContent || '').trim();
                    if (pattern.test(value)) values.push(value);
                }
            }
            return [...new Set(values)];
        }""",
        profile_alt,
    )
    return tuple((str(name), profile_alt) for name in names)


def assert_course_detail(page: Any, settings: Settings) -> None:
    parsed = urlparse(page.url)
    query = parse_qs(parsed.query)
    if (
        parsed.scheme != "https"
        or parsed.hostname != settings.expected_host
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/nonio/inscturmas/inscrever.do"
        or parsed.params
        or set(query) != {"args"}
        or len(query["args"]) != 1
        or not query["args"][0]
        or parsed.fragment
    ):
        raise PortalAssertionError("course detail URL does not match the verified contract")
    expected_heading = f"{settings.course_title} - {settings.course_code}"
    body_lines = {line.strip() for line in page.locator("body").inner_text().splitlines() if line.strip()}
    if expected_heading not in body_lines:
        raise PortalAssertionError("course detail heading is not exact")


def list_course_rows(page: Any, settings: Settings) -> list[CourseListRow]:
    """Read only exact-code rows from the list page, without retaining dynamic URLs."""

    rows: list[CourseListRow] = []
    table_rows = page.locator("tr")
    for index in range(table_rows.count()):
        row = table_rows.nth(index)
        cells = _cells(row)
        if not cells or cells[0] != settings.course_code:
            continue
        title = _course_title(cells, settings)
        if not title:
            continue
        direct_cells = row.locator(":scope > td")
        classes = _selected_profile_classes(direct_cells.nth(3), settings.profile_alt) if direct_cells.count() > 3 else ()
        rows.append(CourseListRow(settings.course_code, title, classes))
    return rows


def find_course_link(page: Any, settings: Settings) -> CourseLink:
    rows = page.locator("tr")
    matches: list[CourseLink] = []
    for index in range(rows.count()):
        row = rows.nth(index)
        cells = _cells(row)
        if not cells or cells[0] != settings.course_code:
            continue
        title = _course_title(cells, settings)
        if not title:
            raise PortalAssertionError("course code row does not have the exact expected title")
        direct_cells = row.locator(":scope > td")
        classes = _selected_profile_classes(direct_cells.nth(3), settings.profile_alt) if direct_cells.count() > 3 else ()
        links = row.locator("a")
        for link_index in range(links.count()):
            href = links.nth(link_index).get_attribute("href") or ""
            parsed = urlparse(urljoin(settings.list_url, href))
            query = parse_qs(parsed.query)
            if (
                parsed.scheme == "https"
                and parsed.hostname == settings.expected_host
                and parsed.port is None
                and parsed.username is None
                and parsed.password is None
                and parsed.path == "/nonio/inscturmas/inscrever.do"
                and not parsed.params
                and set(query) == {"args"}
                and len(query["args"]) == 1
                and bool(query["args"][0])
                and not parsed.fragment
            ):
                matches.append(CourseLink(href, CourseListRow(settings.course_code, title, classes)))
    if len(matches) != 1:
        raise PortalAssertionError("expected exactly one fresh course enrollment link")
    return matches[0]


def class_snapshots(page: Any, settings: Settings) -> tuple[ClassSnapshot, ...]:
    """Extract target class rows, including a zero-seat row with no input."""

    result: list[ClassSnapshot] = []
    rows = page.locator("tr")
    for index in range(rows.count()):
        row = rows.nth(index)
        direct_cells = row.locator(":scope > td")
        cells = [str(value).strip() for value in direct_cells.all_text_contents()]
        if not cells or direct_cells.count() == 0:
            continue
        direct_name = _direct_cell_text(direct_cells.first)
        if direct_name != settings.class_name:
            continue
        cells[0] = direct_name
        inputs = row.locator("input[name='inscrever']")
        if inputs.count() == 0:
            result.append(ClassSnapshot.from_cells(cells, profile_alt=settings.profile_alt))
            continue
        for input_index in range(inputs.count()):
            control = inputs.nth(input_index)
            alt = control.get_attribute("alt") or ""
            result.append(
                ClassSnapshot.from_cells(
                    cells,
                    profile_alt=alt,
                    class_id=control.get_attribute("value"),
                    input_present=True,
                    input_enabled=not control.is_disabled(),
                    checked=control.is_checked(),
                )
            )
    return tuple(result)


def assert_enrollment_form(page: Any, settings: Settings) -> Any:
    form = page.locator("#inscreverFormBean")
    if form.count() != 1:
        raise PortalAssertionError("enrollment form id is missing or duplicated")
    method = (form.get_attribute("method") or "").lower()
    action = urlparse(urljoin(page.url, form.get_attribute("action") or ""))
    if (
        method != "post"
        or action.scheme != "https"
        or action.hostname != settings.expected_host
        or action.port is not None
        or action.username is not None
        or action.password is not None
        or action.path != "/nonio/inscturmas/inscrever.do"
        or action.params
        or parse_qs(action.query) != {"method": ["submeter"]}
        or action.fragment
    ):
        raise PortalAssertionError("enrollment form method or action is not exact")
    save = page.locator("#botaoGravar")
    if save.count() != 1:
        raise PortalAssertionError("save control id is missing or duplicated")
    if (save.get_attribute("type") or "").lower() != "submit" or save.get_attribute("value") != "Gravar":
        raise PortalAssertionError("save control type or value is not exact")
    return form


def authoritative_list_verification(page: Any, settings: Settings) -> bool:
    assert_list_url(page.url, settings)
    from .models import authoritative_enrollment_verified

    return authoritative_enrollment_verified(
        list_course_rows(page, settings),
        course_code=settings.course_code,
        course_title=settings.course_title,
        class_name=settings.class_name,
        profile_alt=settings.profile_alt,
    )


def submit_once_and_verify(page: Any, settings: Settings) -> bool:
    """Stage the exact PL target, click once, and verify only on the list page."""

    timeout_error = _require_playwright()
    form = assert_enrollment_form(page, settings)
    target = form.locator(f"input[name='inscrever'][value='{settings.expected_class_id}']")
    if target.count() != 1 or target.get_attribute("id") != f"inscrever{settings.expected_class_id}":
        raise PortalAssertionError("acknowledged target checkbox is not exact")
    if (
        (target.get_attribute("type") or "").lower() != "checkbox"
        or target.get_attribute("alt") != settings.profile_alt
        or target.is_disabled()
    ):
        raise PortalAssertionError("target checkbox type, profile, or enabled state is not exact")
    other = form.locator(f"input[name='inscrever'][alt='{settings.profile_alt}']")
    target.check()
    if not target.is_checked():
        raise PortalAssertionError("target checkbox did not remain checked")
    for index in range(other.count()):
        checkbox = other.nth(index)
        if checkbox.get_attribute("value") != settings.expected_class_id and checkbox.is_checked():
            raise PortalAssertionError("another PL option remains checked")
    save = form.locator("#botaoGravar")
    if save.is_disabled():
        raise PortalAssertionError("save control is disabled")
    submitted = False
    try:
        with page.expect_navigation(wait_until="domcontentloaded", timeout=15000):
            save.click()
        submitted = True
    except timeout_error:
        # A click may have reached the server even if the browser did not observe navigation.
        pass
    except Exception as exc:
        raise SubmissionUncertain("save result is ambiguous") from exc
    if submitted:
        try:
            if authoritative_list_verification(page, settings):
                return True
        except Exception:
            # Any non-authoritative post-submit page is resolved only by a fresh list check.
            pass
    try:
        page.goto(settings.list_url, wait_until="domcontentloaded")
        if authoritative_list_verification(page, settings):
            return True
    except Exception as exc:
        raise SubmissionUncertain("authoritative list verification failed") from exc
    raise SubmissionUncertain("save was not authoritatively confirmed")
