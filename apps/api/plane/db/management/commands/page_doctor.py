# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Inspect and repair pages whose content was duplicated by a stale client merge.

Usage:

    python manage.py page_doctor --scan                 # report suspect pages
    python manage.py page_doctor --scan --include-deleted
    python manage.py page_doctor --repair <page_id> --dry-run
    python manage.py page_doctor --repair <page_id>
    python manage.py page_doctor --scan-stale           # live content older than history
    python manage.py page_doctor --restore-latest <page_id> --dry-run
    python manage.py page_doctor --restore-latest <page_id>
    python manage.py page_doctor --restore-version <page_id>:<version_id>

A repair rebuilds a single copy of the body, regenerates the document JSON and
Yjs binary through the live service — the page title included, in the Yjs
`title` fragment — and drops the live server's in-memory copy so the fixed
document is what gets served next.

`--scan-stale` covers the other failure mode: a stale client copy can overwrite
content that was just written through the API (a CLI re-upload), leaving the page
showing an older state while the newer one survives only as a history entry.
`--restore-latest` puts the newest history entry back as the live content — but
only when that entry is not itself the product of an overwrite: on a clobbered
page the newest entry IS the stale copy, so restoring it would keep the loss.
The command refuses that case and points at `--restore-version`, which restores
one named entry (pick the last one written by the importing client).
"""

# Python imports
import base64

# Third party imports
from django.core.management.base import BaseCommand, CommandError

# Module imports
from plane.db.models import Page, PageVersion
from plane.utils.page_content import (
    convert_page_html_to_formats,
    invalidate_live_document,
    page_content_fingerprint,
    page_content_text_length,
)
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
            "--scan-stale",
            action="store_true",
            help="List pages whose live content is older than their newest history entry",
        )
        parser.add_argument(
            "--restore-latest",
            metavar="PAGE_ID",
            help="Make a page's newest history entry its live content again (refuses an overwrite entry)",
        )
        parser.add_argument(
            "--restore-version",
            metavar="PAGE_ID:VERSION_ID",
            help="Restore one named history entry as the live content (use for clobbered pages)",
        )
        parser.add_argument(
            "--scan-links",
            action="store_true",
            help="List pages holding half-converted markdown links/images ([text](url), ![alt](url))",
        )
        parser.add_argument(
            "--fix-links",
            metavar="PAGE_ID",
            help="Fold half-converted markdown links/images on one page into proper markup",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="With --repair/--fix-links/--restore-latest/--restore-version: write nothing",
        )
        parser.add_argument(
            "--include-deleted",
            action="store_true",
            help="With --scan/--scan-stale: include archived and soft-deleted pages",
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
        if options["scan_stale"]:
            self.scan_stale_current(include_deleted=options["include_deleted"])
            return
        if options["restore_latest"]:
            self.restore_latest_version(options["restore_latest"], dry_run=options["dry_run"])
            return
        if options["restore_version"]:
            page_id, _, version_id = options["restore_version"].partition(":")
            if not page_id or not version_id:
                raise CommandError("--restore-version expects <page_id>:<version_id>")
            self.restore_named_version(page_id, version_id, dry_run=options["dry_run"])
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

    # --- stale live content ------------------------------------------------
    def scan_stale_current(self, include_deleted: bool = False):
        queryset = Page.all_objects if include_deleted else Page.objects
        suspects = []
        for page in queryset.only("id", "name", "description_html").iterator():
            versions = list(
                PageVersion.objects.filter(page_id=page.id)
                .order_by("created_at")
                .only("id", "created_at", "owned_by_id", "description_html")
            )
            if len(versions) < 2:
                continue
            current = page_content_fingerprint(page.description_html)
            newest = versions[-1]
            if page_content_fingerprint(newest.description_html) == current:
                continue
            older = [v for v in versions[:-1] if page_content_fingerprint(v.description_html) == current]
            if older:
                suspects.append((page, older[-1], newest))

        if not suspects:
            self.stdout.write(self.style.SUCCESS("No page holds content older than its newest history entry."))
            return

        self.stdout.write(
            self.style.WARNING(
                f"{len(suspects)} page(s) hold content older than their newest history entry "
                f"— a stale client copy overwrote a newer write:"
            )
        )
        for page, matching, newest in suspects:
            if page.archived_at:
                flags = " [archived]"
            else:
                flags = ""
            self.stdout.write(
                f"  {page.id}{flags}  html={len(page.description_html or '')}  matches {str(matching.id)[:8]} "
                f"({matching.created_at:%Y-%m-%d %H:%M}), newest is {str(newest.id)[:8]} "
                f"({newest.created_at:%Y-%m-%d %H:%M})  "
                f"{page.name[:50]}"
            )
        self.stdout.write("\nRestore the newest entry with:  python manage.py page_doctor --restore-latest <page_id>")

    def restore_latest_version(self, page_id: str, dry_run: bool = False):
        try:
            page = Page.all_objects.get(id=page_id)
        except (Page.DoesNotExist, ValueError):
            raise CommandError(f"Page {page_id} not found")

        versions = list(
            PageVersion.objects.filter(page_id=page.id)
            .order_by("created_at")
            .only("id", "created_at", "description_html")
        )
        if not versions:
            raise CommandError(f"Page {page_id} has no history entry to restore")

        newest = versions[-1]
        earlier = {page_content_fingerprint(v.description_html) for v in versions[:-1]}
        if page_content_fingerprint(newest.description_html) in earlier:
            # The newest entry reproduces an earlier state: it is the overwrite
            # itself, so restoring it would keep the loss.
            raise CommandError(
                f"Page {page_id}: the newest history entry ({str(newest.id)[:8]}) reproduces an earlier state, "
                "so it is an overwrite rather than the content to restore. Pick the last entry written by the "
                "importing client and use --restore-version <page_id>:<version_id>."
            )

        self.restore_version(page, newest, dry_run=dry_run)

    def restore_named_version(self, page_id: str, version_id: str, dry_run: bool = False):
        try:
            page = Page.all_objects.get(id=page_id)
        except (Page.DoesNotExist, ValueError):
            raise CommandError(f"Page {page_id} not found")
        try:
            version = PageVersion.objects.get(id=version_id, page_id=page.id)
        except (PageVersion.DoesNotExist, ValueError):
            raise CommandError(f"Page {page_id} has no history entry {version_id}")

        self.restore_version(page, version, dry_run=dry_run)

    def restore_version(self, page, version, dry_run: bool = False):
        self.stdout.write(
            f"Page {page.id} ({page.name[:50]})  current text={page_content_text_length(page.description_html)}  "
            f"restoring entry {str(version.id)[:8]} ({version.created_at:%Y-%m-%d %H:%M}, "
            f"text={page_content_text_length(version.description_html)})"
        )
        if dry_run:
            self.stdout.write("Dry run: nothing written.")
            return

        page.description_html = version.description_html
        page.description_json = version.description_json
        page.description_binary = version.description_binary
        page.description_stripped = version.description_stripped
        page.save(
            update_fields=[
                "description_html",
                "description_json",
                "description_binary",
                "description_stripped",
                "updated_at",
            ]
        )
        page.refresh_from_db()
        invalidate_live_document(str(page.id), page_content_text_length(page.description_html))
        self.stdout.write(
            self.style.SUCCESS(
                f"Restored {page.id} from {str(version.id)[:8]}: "
                f"text={page_content_text_length(page.description_html)} binary={len(page.description_binary or b'')}"
            )
        )

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
        """Rebuild json/binary from the repaired html (page title included), store it, drop live's copy."""
        converted = convert_page_html_to_formats(repaired_html, page.name)
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
        # A repair is not an edit: it must not take the page over from whoever
        # last saved it (`save()` attributes the write to the current user, and
        # in a management command there is none — the attribution would be lost).
        page.save(disable_auto_set_user=True)

        invalidate_live_document(str(page.id), page_content_text_length(page.description_html))
