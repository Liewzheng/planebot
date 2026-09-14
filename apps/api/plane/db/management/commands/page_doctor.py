# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Inspect and repair pages whose content was duplicated by a stale client merge.

Usage:

    python manage.py page_doctor --scan                 # report suspect pages
    python manage.py page_doctor --scan --include-deleted
    python manage.py page_doctor --repair <page_id> --dry-run
    python manage.py page_doctor --repair <page_id>

A repair rebuilds a single copy of the body, regenerates the document JSON and
Yjs binary through the live service and drops the live server's in-memory copy
so the fixed document is what gets served next.
"""

# Python imports
import base64

# Third party imports
from django.core.management.base import BaseCommand, CommandError

# Module imports
from plane.db.models import Page
from plane.utils.page_content import convert_page_html_to_formats, invalidate_live_document
from plane.utils.page_duplication import (
    CannotDeduplicate,
    deduplicate_page_html,
    is_document_duplicated,
    line_stats,
)
from plane.utils.page_markdown_links import has_broken_markdown_links, normalize_markdown_links


class Command(BaseCommand):
    help = "Report pages whose body looks duplicated and repair them into a single copy"

    def add_arguments(self, parser):
        parser.add_argument("--scan", action="store_true", help="List pages whose body looks duplicated")
        parser.add_argument("--repair", metavar="PAGE_ID", help="Repair one page (id from --scan)")
        parser.add_argument(
            "--scan-links",
            action="store_true",
            help="List pages holding half-converted markdown links ([text](url))",
        )
        parser.add_argument(
            "--fix-links",
            metavar="PAGE_ID",
            help="Fold half-converted markdown links on one page into proper anchors",
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="With --repair/--fix-links: show the plan, write nothing"
        )
        parser.add_argument(
            "--include-deleted",
            action="store_true",
            help="With --scan: include archived and soft-deleted pages",
        )

    def handle(self, *args, **options):
        if options["fix_links"]:
            self.fix_links_page(options["fix_links"], dry_run=options["dry_run"])
            return
        if options["scan_links"]:
            self.scan_broken_links(include_deleted=options["include_deleted"])
            return
        if options["repair"]:
            self.repair_page(options["repair"], dry_run=options["dry_run"])
            return
        self.scan_pages(include_deleted=options["include_deleted"])

    # --- scanning ---------------------------------------------------------
    def scan_pages(self, include_deleted: bool = False):
        queryset = Page.all_objects if include_deleted else Page.objects
        suspects = []
        for page in queryset.only("id", "name", "description_html", "archived_at", "deleted_at").iterator():
            html = page.description_html or ""
            if not is_document_duplicated(html):
                continue
            suspects.append((page, line_stats(html)))

        if not suspects:
            self.stdout.write(self.style.SUCCESS("No page looks duplicated."))
            return

        self.stdout.write(self.style.WARNING(f"{len(suspects)} page(s) look duplicated:"))
        for page, stats in suspects:
            flags = []
            if page.archived_at:
                flags.append("archived")
            if page.deleted_at:
                flags.append("deleted")
            suffix = f" [{', '.join(flags)}]" if flags else ""
            self.stdout.write(
                f"  {page.id}  html={len(page.description_html or '')}  "
                f"lines={stats['total_lines']}  unique={stats['unique_lines']}  "
                f"ratio={stats['unique_ratio']}{suffix}  {page.name[:60]}"
            )
        self.stdout.write("\nRepair one of them with:  python manage.py page_doctor --repair <page_id>")

    # --- repairing --------------------------------------------------------
    def repair_page(self, page_id: str, dry_run: bool = False):
        try:
            page = Page.all_objects.get(id=page_id)
        except (Page.DoesNotExist, ValueError):
            raise CommandError(f"Page {page_id} not found")

        html = page.description_html or ""
        before = line_stats(html)
        self.stdout.write(
            f"Page {page.id} ({page.name[:60]})  html={len(html)}  "
            f"lines={before['total_lines']}  unique={before['unique_lines']}  ratio={before['unique_ratio']}"
        )

        try:
            repaired_html, report = deduplicate_page_html(html)
        except CannotDeduplicate as error:
            raise CommandError(f"Cannot repair page {page_id}: {error}")

        self.stdout.write(
            self.style.WARNING(
                f"Plan: {report['strategy']} of {report['copies']} copy(ies) "
                f"({report['before_chars']} -> {report['after_chars']} chars)"
                + (f", grafted {report['grafted_blocks']} block(s)" if report.get("grafted_blocks") else "")
            )
        )

        if dry_run:
            self.stdout.write("Dry run: nothing written.")
            return

        self.write_repaired_html(page, repaired_html)

        page.refresh_from_db()
        after = line_stats(page.description_html or "")
        self.stdout.write(
            self.style.SUCCESS(
                f"Repaired {page.id}: html={len(page.description_html or '')} "
                f"binary={len(page.description_binary or b'')} "
                f"lines={after['total_lines']} unique={after['unique_lines']} ratio={after['unique_ratio']}"
            )
        )

    # --- half-converted markdown links ------------------------------------
    def scan_broken_links(self, include_deleted: bool = False):
        queryset = Page.all_objects if include_deleted else Page.objects
        suspects = []
        for page in queryset.only("id", "name", "description_html").iterator():
            html = page.description_html or ""
            if has_broken_markdown_links(html):
                suspects.append(page)

        if not suspects:
            self.stdout.write(self.style.SUCCESS("No page holds half-converted markdown links."))
            return

        self.stdout.write(self.style.WARNING(f"{len(suspects)} page(s) hold half-converted markdown links:"))
        for page in suspects:
            self.stdout.write(f"  {page.id}  html={len(page.description_html or '')}  {page.name[:60]}")
        self.stdout.write("\nFix one with:  python manage.py page_doctor --fix-links <page_id>")

    def fix_links_page(self, page_id: str, dry_run: bool = False):
        try:
            page = Page.all_objects.get(id=page_id)
        except (Page.DoesNotExist, ValueError):
            raise CommandError(f"Page {page_id} not found")

        html = page.description_html or ""
        repaired_html, repaired_links = normalize_markdown_links(html)
        self.stdout.write(f"Page {page.id} ({page.name[:60]})  html={len(html)}")

        if not repaired_links:
            self.stdout.write(self.style.SUCCESS("Nothing to fix: no half-converted markdown links found."))
            return

        self.stdout.write(
            self.style.WARNING(
                f"Plan: fold {repaired_links} half-converted link(s) into anchors "
                f"({len(html)} -> {len(repaired_html)} chars)"
            )
        )

        if dry_run:
            self.stdout.write("Dry run: nothing written.")
            return

        self.write_repaired_html(page, repaired_html)

        page.refresh_from_db()
        self.stdout.write(
            self.style.SUCCESS(
                f"Fixed {page.id}: {repaired_links} link(s) repaired, "
                f"html={len(page.description_html or '')} binary={len(page.description_binary or b'')}"
            )
        )

    # --- shared write path ------------------------------------------------
    def write_repaired_html(self, page, repaired_html: str):
        """Rebuild json/binary from the repaired html, store it, drop live's copy."""
        converted = convert_page_html_to_formats(repaired_html)
        encoded_binary = converted.get("description_binary")
        if not encoded_binary:
            raise CommandError(
                "live service could not convert the repaired document "
                "(check that /convert-document/ is reachable and accepts documents of this size)"
            )
        binary = base64.b64decode(encoded_binary)

        # A healthy conversion yields a Y.Doc binary in the same order of
        # magnitude as the html; a tiny one means the html was not parsed.
        if len(binary) < 0.3 * len(repaired_html):
            raise CommandError(
                f"suspicious conversion: html={len(repaired_html)} binary={len(binary)} — refusing to write"
            )

        page.description_html = repaired_html
        page.description_json = converted.get("description_json")
        page.description_binary = binary
        page.save()

        invalidate_live_document(str(page.id))
