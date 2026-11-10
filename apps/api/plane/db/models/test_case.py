# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Test-case and test-run models (STD-SW-003 / REC-SW-001).

The layer holds the writing side of the standard:

* :class:`TestCaseModule` — the registry of module codes (STD-SW-003 §4.2,
  discipline 3: one code stands for one group of functions, registered at
  the head of the project's case set).
* :class:`TestCase` — the case header (the eight mandatory items, plus
  the two recommended text columns of §4.1: the related feature and the
  remarks) and its status ``active`` / ``void``.
* :class:`TestCaseStep` — the ordered step table.
* :class:`TestRun` — a batch (one continuous execution) with its version,
  environment, executor and date, and — for a re-test batch — the link to
  the batch it re-tests (§6.2 rule 2).
* :class:`TestRunCase` — a case inside a batch. It carries a **snapshot**
  of the case header, so editing the case later never rewrites the history
  of a batch that was already executed.
* :class:`TestRunStepRecord` — the verdict and the measured value of one
  step, next to a snapshot of the step itself.
* :class:`TestCaseIssue` — the link between a case and a work item.

Numbering lives here as well: :func:`allocate_test_case_number` hands out
``TC-<CODE>-<NNN>`` per project × module and :func:`allocate_test_run_number`
hands out ``RUN-<YYYYMMDD>-<NN>`` per project × date. Both serialise on an
existing row with ``select_for_update``, the way ``IssueSequence`` does for
work items — no new concurrency primitive is introduced.
"""

# Django imports
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction

# Module imports
from plane.db.testcase_numbering import (
    CASE_SEQUENCE_MAX,
    RUN_SEQUENCE_MAX,
    format_case_number,
    format_run_number,
    is_valid_module_code,
)
from plane.db.testcase_validity import (
    CONCLUSION_IS_FINAL,
    DUPLICATE_CASE_NUMBER,
    RETEST_OF_CYCLE,
    RETEST_OF_PROJECT_MISMATCH,
    RETEST_OF_SELF,
    RUN_CASE_CONCLUSION_FAIL,
    RUN_CASE_CONCLUSION_MANUAL,
    RUN_CASE_CONCLUSION_NA,
    RUN_CASE_CONCLUSION_PASS,
    RUN_STATUS_COMPLETED,
    RUN_STATUS_DRAFT,
    RUN_STATUS_IN_PROGRESS,
    STEP_VERDICT_FAIL,
    STEP_VERDICT_PASS,
    STEP_VERDICT_UNSET,
)

# Module imports
from .project import Project, ProjectBaseModel


class TestCaseModule(ProjectBaseModel):
    """Registry row for a module code.

    A code is 2–6 uppercase letters and is unique inside a project. The
    description states which group of functions the code covers — the
    standard requires the code to be registered before it is used, so that
    nobody has to guess later what ``CLI`` meant.
    """

    code = models.CharField(max_length=6)
    description = models.TextField()

    class Meta:
        verbose_name = "Test Case Module"
        verbose_name_plural = "Test Case Modules"
        db_table = "test_case_modules"
        ordering = ("code",)
        constraints = [
            models.UniqueConstraint(
                fields=["project", "code"],
                condition=models.Q(deleted_at__isnull=True),
                name="test_case_module_unique_project_code_when_deleted_at_null",
            )
        ]

    def __str__(self):
        return f"{self.code} <{self.project.name}>"

    def clean(self):
        super().clean()
        # ``save()`` normalises before it validates; ``clean()`` has to do
        # the same, otherwise ``full_clean()`` (what a serializer calls)
        # would reject a lowercase code that ``save()`` stores happily.
        self.code = (self.code or "").strip().upper()
        self.description = (self.description or "").strip()
        self._validate_code_and_description()

    def save(self, *args, **kwargs):
        self.code = (self.code or "").strip().upper()
        self.description = (self.description or "").strip()
        self._validate_code_and_description()

        if self._state.adding:
            self._validate_code_available()
        else:
            self._assert_code_immutable()

        super(TestCaseModule, self).save(*args, **kwargs)

    def delete(self, using=None, soft=True, *args, **kwargs):
        """Refuse to remove a module that already carries history.

        The code is a permanent key inside ``TC-<CODE>-<NNN>``. Removing a
        module that has cases (or that has issued a number) would let the
        same number be handed to different content later, which the
        standard forbids — a voided case keeps its number. A module that
        never issued a number may go, and its code may be registered again
        because nothing was ever numbered under it.
        """
        if TestCase.all_objects.filter(module_id=self.pk).exists():
            raise ValidationError(
                "A module that carries test cases cannot be deleted. Void the "
                "cases first; their numbers stay reserved."
            )
        if TestCaseSequence.all_objects.filter(module_id=self.pk).exists():
            raise ValidationError(
                "A module that already issued a number cannot be deleted; "
                "the number would be handed out a second time."
            )
        return super(TestCaseModule, self).delete(using=using, soft=soft, *args, **kwargs)

    def _validate_code_and_description(self):
        if not is_valid_module_code(self.code):
            raise ValidationError(
                {"code": "The module code must be 2 to 6 uppercase letters."}
            )
        if not self.description:
            raise ValidationError(
                {"description": "The module description is required."}
            )

    def _validate_code_available(self):
        if (
            TestCaseModule.objects.filter(project=self.project, code=self.code)
            .exclude(pk=self.pk)
            .exists()
        ):
            raise ValidationError(
                {"code": "The module code is already used in this project."}
            )

    def _assert_code_immutable(self):
        original = (
            TestCaseModule.all_objects.filter(pk=self.pk)
            .values_list("code", flat=True)
            .first()
        )
        if original is None or original == self.code:
            return
        if self._has_issued_numbers():
            raise ValidationError(
                "The code of a module that carries test cases cannot be changed; "
                "the code would change the meaning of the numbers issued under it."
            )

    def _has_issued_numbers(self) -> bool:
        return TestCase.all_objects.filter(module_id=self.pk).exists() or (
            TestCaseSequence.all_objects.filter(module_id=self.pk).exists()
        )


class TestCaseSequence(ProjectBaseModel):
    """Counter of the test-case numbers of one module (per project).

    The row is the lock target of the allocator — the same role
    ``IssueSequence`` plays for work items.
    """

    module = models.ForeignKey(
        TestCaseModule,
        on_delete=models.CASCADE,
        related_name="sequences",
    )
    sequence = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Test Case Sequence"
        verbose_name_plural = "Test Case Sequences"
        db_table = "test_case_sequences"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["module"],
                name="test_case_sequence_unique_module",
            )
        ]

    def __str__(self):
        return f"{self.module.code}@{self.sequence}"


class TestRunSequence(ProjectBaseModel):
    """Counter of the batch numbers of one project on one day."""

    run_date = models.DateField()
    sequence = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Test Run Sequence"
        verbose_name_plural = "Test Run Sequences"
        db_table = "test_run_sequences"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["project", "run_date"],
                name="test_run_sequence_unique_project_date",
            )
        ]

    def __str__(self):
        return f"{self.project.name}@{self.run_date}#{self.sequence}"


class TestCase(ProjectBaseModel):
    """A test case: the eight mandatory items of the case header.

    The two recommended fields of STD-SW-003 §4.1 are text columns next to
    them: ``related_feature`` (field 10 — what the case stands for: the
    requirement item, change ticket, commit or design version and where
    the value was read from) and ``remarks`` (field 11 — deviations,
    findings, uncovered scope, unverified items and their source). Both
    sit next to the work-item links of :class:`TestCaseIssue`: the links
    are rows that move with the work item, the two columns are what the
    author wrote down, and a case may carry either, both or neither.

    The number (``TC-<CODE>-<NNN>``) is handed out by
    :func:`allocate_test_case_number` when the case is created and never
    changes afterwards — not when the case is voided, and not when the
    module code is touched (a module that carries cases cannot change its
    code either).
    """

    PRIORITY_CHOICES = (
        ("P0", "P0"),
        ("P1", "P1"),
        ("P2", "P2"),
        ("P3", "P3"),
    )
    STATUS_CHOICES = (
        ("active", "Active"),
        ("void", "Void"),
    )

    module = models.ForeignKey(
        TestCaseModule,
        on_delete=models.PROTECT,
        related_name="test_cases",
    )
    sequence_id = models.PositiveIntegerField(null=True, blank=True)
    number = models.CharField(max_length=32, blank=True, default="", db_index=True)

    title = models.CharField(max_length=255, blank=True, default="")
    purpose = models.TextField(blank=True, default="")
    object_overview = models.TextField(blank=True, default="")
    priority = models.CharField(max_length=2, choices=PRIORITY_CHOICES, blank=True, default="")
    preconditions = models.TextField(blank=True, default="")
    environment = models.TextField(
        blank=True,
        default="",
        verbose_name="Environment And Version",
    )

    # STD-SW-003 §4.1 field 10 (推荐). Free text, deliberately: the
    # standard names "需求条目、变更单号、提交号或设计稿版本" plus the source
    # the value was read from (§5.7), which is a sentence, not a row. It
    # sits next to the work-item links of ``TestCaseIssue`` — a case may
    # carry both, and neither replaces the other: the link is a living
    # reference to a row, the text is what the case was written against
    # at that moment.
    related_feature = models.TextField(
        blank=True,
        default="",
        verbose_name="Related Feature",
        help_text=(
            "The requirement item, change ticket, commit or design version "
            "this case stands for, plus where the value was read from "
            "(STD-SW-003 §4.1 field 10, §5.7). Free text; it sits next to "
            "the work-item links of TestCaseIssue and does not replace them."
        ),
    )
    # STD-SW-003 §4.1 field 11 (推荐).
    remarks = models.TextField(
        blank=True,
        default="",
        verbose_name="Remarks",
        help_text=(
            "Deviations, incidental findings, uncovered scope, unverified "
            "items and the source they rest on (STD-SW-003 §4.1 field 11, "
            "§5.12)."
        ),
    )

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="active")

    class Meta:
        verbose_name = "Test Case"
        verbose_name_plural = "Test Cases"
        db_table = "test_cases"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["project", "number"],
                name="test_case_unique_project_number",
            ),
            models.UniqueConstraint(
                fields=["module", "sequence_id"],
                name="test_case_unique_module_sequence",
            ),
        ]

    def __str__(self):
        return f"{self.number} {self.title}"

    def clean(self):
        super().clean()
        self._validate_module_belongs_to_project()

    def save(self, *args, **kwargs):
        self._validate_module_belongs_to_project()

        if self._state.adding:
            self._assign_number()
        else:
            self._assert_number_immutable()

        super(TestCase, self).save(*args, **kwargs)

    def _validate_module_belongs_to_project(self):
        if (
            self.module_id
            and self.project_id
            and self.module.project_id != self.project_id
        ):
            raise ValidationError(
                {"module": "The module must belong to the same project as the test case."}
            )

    def _assign_number(self):
        if self.sequence_id is None:
            self.sequence_id = allocate_test_case_number(self.module)
        try:
            expected = format_case_number(self.module.code, self.sequence_id)
        except ValueError as error:
            raise ValidationError({"module": str(error)}) from error
        if self.number and self.number != expected:
            raise ValidationError(
                {"number": f"The number of the test case must be {expected}."}
            )
        self.number = expected

    def _assert_number_immutable(self):
        original = (
            TestCase.all_objects.filter(pk=self.pk)
            .values("module", "sequence_id", "number")
            .first()
        )
        if original is None:
            return
        if self.module_id != original["module"]:
            raise ValidationError(
                {"module": "The module of a test case cannot be changed after it is created."}
            )
        if self.sequence_id != original["sequence_id"]:
            raise ValidationError(
                {"number": "The number of a test case cannot be changed after it is created."}
            )
        if self.number != original["number"]:
            raise ValidationError(
                {"number": "The number of a test case cannot be changed after it is created."}
            )


class TestCaseStep(ProjectBaseModel):
    """One row of the step table of a case."""

    test_case = models.ForeignKey(
        TestCase,
        on_delete=models.CASCADE,
        related_name="steps",
    )
    sequence_id = models.PositiveIntegerField()
    action = models.TextField(blank=True, default="")
    expected_result = models.TextField(blank=True, default="")

    class Meta:
        verbose_name = "Test Case Step"
        verbose_name_plural = "Test Case Steps"
        db_table = "test_case_steps"
        ordering = ("sequence_id",)
        constraints = [
            models.UniqueConstraint(
                fields=["test_case", "sequence_id"],
                name="test_case_step_unique_case_sequence",
            )
        ]

    def __str__(self):
        return f"{self.test_case.number}#{self.sequence_id}"

    def save(self, *args, **kwargs):
        self._validate_case_belongs_to_project()
        super(TestCaseStep, self).save(*args, **kwargs)

    def _validate_case_belongs_to_project(self):
        if (
            self.test_case_id
            and self.project_id
            and self.test_case.project_id != self.project_id
        ):
            raise ValidationError(
                {"test_case": "The test case must belong to the same project as the step."}
            )


class TestRun(ProjectBaseModel):
    """A batch: one continuous execution of test cases.

    The batch header holds the four items the standard requires — the
    tested version (number + build + commit), the environment, the
    executor and the execution date. The number is handed out by
    :func:`allocate_test_run_number` and never changes.

    ``retest_of`` is the fifth header item of a **re-test** batch: §6.2
    rule 2 says 「补测追加新批次，批次头写「补测：`RUN-20261109-01`」指向原
    批次；不往旧批次里回填结论」. The link points at the original batch,
    which is normally already completed — pointing at a completed batch is
    the ordinary case, not an error.
    """

    STATUS_CHOICES = (
        (RUN_STATUS_DRAFT, "Draft"),
        (RUN_STATUS_IN_PROGRESS, "In Progress"),
        (RUN_STATUS_COMPLETED, "Completed"),
    )

    sequence_id = models.PositiveIntegerField(null=True, blank=True)
    number = models.CharField(max_length=32, blank=True, default="", db_index=True)

    run_date = models.DateField()
    version_number = models.CharField(max_length=64, blank=True, default="")
    build_number = models.CharField(max_length=64, blank=True, default="")
    commit_hash = models.CharField(max_length=64, blank=True, default="")
    environment = models.TextField(blank=True, default="")
    executed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="test_runs",
    )
    executed_by_name = models.TextField(
        blank=True,
        default="",
        verbose_name="Executed By Name",
        help_text=(
            "Text snapshot of the executor. The foreign key is SET_NULL, so "
            "the name of a deleted user would be lost from the history of "
            "the batch; the snapshot keeps it."
        ),
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=RUN_STATUS_DRAFT,
    )
    # STD-SW-003 §6.2 rule 2. ``SET_NULL``: deleting the original batch
    # must not delete the re-test that followed it — the re-test is a
    # record of its own, and its snapshot of the cases is what it is for.
    # The link is checked by ``_validate_retest_of``: same project, never
    # itself, never a ring.
    retest_of = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="retests",
        help_text=(
            "The batch this one re-tests (STD-SW-003 §6.2 rule 2: a re-test "
            "appends a new batch that names the original one). The original "
            "is usually completed already. Same project, never itself, and "
            "the chain never runs in a ring."
        ),
    )

    class Meta:
        verbose_name = "Test Run"
        verbose_name_plural = "Test Runs"
        db_table = "test_runs"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["project", "number"],
                name="test_run_unique_project_number",
            )
        ]

    def __str__(self):
        return f"{self.number} <{self.project.name}>"

    def clean(self):
        super().clean()
        self._validate_retest_of()

    def save(self, *args, **kwargs):
        self._validate_retest_of()

        if self._state.adding:
            self._assign_number()
        else:
            self._assert_identity_immutable()
            self._assert_version_fixed()
            self._assert_not_frozen()

        update_fields = self._materialise_update_fields(kwargs)
        self._sync_executed_by_name(update_fields)
        self._keep_snapshot_in_update_fields(kwargs, update_fields)

        super(TestRun, self).save(*args, **kwargs)

    def _materialise_update_fields(self, kwargs):
        """Turn ``update_fields`` into a list exactly once.

        Django accepts any iterable for ``update_fields`` (it converts it
        to a frozenset itself), so a one-shot iterator must not be
        consumed twice: a membership test would see an empty iterable
        and Django would be handed a truncated one.
        """
        update_fields = kwargs.get("update_fields")
        if update_fields is None:
            return None
        fields = list(update_fields)
        kwargs["update_fields"] = fields
        return fields

    def _sync_executed_by_name(self, update_fields=None):
        """Keep a text snapshot of the executor next to the foreign key.

        The foreign key is ``SET_NULL``: a deleted user would leave the
        batch without an executor, and a completed batch would become
        retroactively invalid. The snapshot keeps the name of the person
        who executed the batch. It is (re)written when the executor
        changes; it survives the user row.
        """
        if self._state.adding:
            if self.executed_by_id and not self.executed_by_name:
                self.executed_by_name = self._executor_display_name()
            return

        if update_fields is not None and "executed_by" not in update_fields:
            # The foreign key is not part of this write — the snapshot
            # must not move with it.
            return

        # The row is fetched, not the flat value of the column: a flat
        # ``None`` cannot be told apart from "the row does not exist",
        # and the snapshot would then never be filled on the ordinary
        # flow of assigning an executor to a batch that had none.
        row = (
            TestRun.all_objects.filter(pk=self.pk)
            .values("executed_by_id")
            .first()
        )
        if row is None:
            return
        stored = row["executed_by_id"]

        if self.executed_by_id != stored:
            # The executor of the batch changed — the snapshot follows.
            # A batch whose executor was deleted and is now assigned to
            # somebody else gets the new name, not the deleted one.
            self.executed_by_name = (
                self._executor_display_name() if self.executed_by_id else ""
            )
        elif not self.executed_by_name and self.executed_by_id:
            self.executed_by_name = self._executor_display_name()

    def _keep_snapshot_in_update_fields(self, kwargs, update_fields=None):
        """Keep the snapshot in step with a partial update.

        Django only writes the fields listed in ``update_fields``; a
        caller that moves the executor with
        ``save(update_fields=["executed_by"])`` would otherwise leave the
        old name next to the new person.
        """
        if update_fields is None:
            return
        if "executed_by" in update_fields and "executed_by_name" not in update_fields:
            update_fields.append("executed_by_name")
        kwargs["update_fields"] = update_fields

    def _validate_retest_of(self):
        """Check the link of a re-test batch (STD-SW-003 §6.2 rule 2).

        Three things are refused:

        * a link across projects — a batch of one project may not describe
          the history of another (every other guard of this file draws the
          same line);
        * a link to itself;
        * a link that closes a ring, whether the ring runs through this
          batch or between two other rows.

        A link to a **completed** batch is the normal case: §6.2 rule 2
        appends the re-test after the original one. A link to an open batch
        is allowed too — a re-test may be opened while the first batch is
        still being written.
        """
        if not self.retest_of_id:
            return

        if self.pk is not None and self.retest_of_id == self.pk:
            raise ValidationError(
                {"retest_of": f"{RETEST_OF_SELF}: a batch cannot re-test itself."}
            )

        seen = set()
        current_id = self.retest_of_id
        while current_id is not None:
            if self.pk is not None and current_id == self.pk:
                raise ValidationError(
                    {
                        "retest_of": (
                            f"{RETEST_OF_CYCLE}: the re-test chain would run "
                            "in a ring."
                        )
                    }
                )
            if current_id in seen:
                # A ring that does not contain this batch: two other rows
                # point at each other. Refusing it keeps the walk finite
                # and keeps a broken chain out.
                raise ValidationError(
                    {
                        "retest_of": (
                            f"{RETEST_OF_CYCLE}: the re-test chain would run "
                            "in a ring."
                        )
                    }
                )
            seen.add(current_id)

            row = (
                TestRun.all_objects.filter(pk=current_id)
                .values("project_id", "retest_of_id")
                .first()
            )
            if row is None:
                raise ValidationError(
                    {"retest_of": "The batch this one re-tests does not exist."}
                )
            if self.project_id and row["project_id"] != self.project_id:
                raise ValidationError(
                    {
                        "retest_of": (
                            f"{RETEST_OF_PROJECT_MISMATCH}: the batch this one "
                            "re-tests must belong to the same project."
                        )
                    }
                )
            current_id = row["retest_of_id"]

    def _executor_display_name(self):
        user = self.executed_by
        if user is None:
            return ""
        return (
            user.display_name or user.full_name or user.email or ""
        ).strip()

    def _assign_number(self):
        if self.sequence_id is None:
            self.sequence_id = allocate_test_run_number(self.project, self.run_date)
        try:
            expected = format_run_number(self.run_date, self.sequence_id)
        except ValueError as error:
            raise ValidationError({"run_date": str(error)}) from error
        if self.number and self.number != expected:
            raise ValidationError(
                {"number": f"The number of the batch must be {expected}."}
            )
        self.number = expected

    def _assert_identity_immutable(self):
        original = (
            TestRun.all_objects.filter(pk=self.pk)
            .values("run_date", "sequence_id", "number")
            .first()
        )
        if original is None:
            return
        if self.run_date != original["run_date"]:
            raise ValidationError(
                {"run_date": "The execution date of a batch cannot be changed after it is created."}
            )
        if self.sequence_id != original["sequence_id"]:
            raise ValidationError(
                {"number": "The number of a batch cannot be changed after it is created."}
            )
        if self.number != original["number"]:
            raise ValidationError(
                {"number": "The number of a batch cannot be changed after it is created."}
            )

    def _assert_version_fixed(self):
        """One batch stands for one tested version (STD-SW-003 §5).

        The version may still be filled in while the batch is empty, but
        once it is recorded it is frozen — a re-test against another build
        opens a new batch instead of rewriting an old one.
        """
        original = (
            TestRun.all_objects.filter(pk=self.pk)
            .values("version_number", "build_number", "commit_hash")
            .first()
        )
        if original is None:
            return
        for field in ("version_number", "build_number", "commit_hash"):
            recorded = original[field]
            if recorded and getattr(self, field) != recorded:
                raise ValidationError(
                    {
                        field: "The tested version of a batch cannot be changed; "
                        "open a new batch for a re-test."
                    }
                )

    def _assert_not_frozen(self):
        """Refuse to change the header of a batch that is already completed.

        A conclusion that is written into a batch is never changed
        afterwards; a re-test opens a new batch instead.
        """
        original = (
            TestRun.all_objects.filter(pk=self.pk)
            .values(
                "status",
                "version_number",
                "build_number",
                "commit_hash",
                "environment",
                "executed_by",
                "executed_by_name",
                "retest_of",
            )
            .first()
        )
        if original is None:
            return
        if original["status"] != RUN_STATUS_COMPLETED:
            return
        if self.status != RUN_STATUS_COMPLETED:
            raise ValidationError(
                "A completed batch cannot be reopened; open a new batch for a re-test."
            )
        changed = [
            field
            for field in (
                "version_number",
                "build_number",
                "commit_hash",
                "environment",
                "executed_by_name",
            )
            if getattr(self, field) != original[field]
        ]
        if self.executed_by_id != original["executed_by"]:
            changed.append("executed_by")
        if self.retest_of_id != original["retest_of"]:
            # The link to the original batch is part of the header as well
            # (§6.2 rule 2): a completed batch does not gain or lose it.
            changed.append("retest_of")
        if changed:
            raise ValidationError(
                "A completed batch is frozen; the following fields cannot be "
                f"changed any more: {', '.join(changed)}."
            )


class TestRunCase(ProjectBaseModel):
    """A case inside a batch, with the snapshot of its header.

    The snapshot is the point of this model: the case may be edited (or
    voided) later, but the batch keeps what was executed. The step table
    is snapshotted as well, in :class:`TestRunStepRecord`.

    Storage contract of ``evidence``
    --------------------------------
    ``evidence`` holds the **Markdown source** of the evidence, exactly
    as it was typed: it is not converted to HTML and the markup is not
    stripped. A serializer hands the source out unchanged — the front end
    renders it. The rules of :mod:`plane.db.testcase_validity` read the
    source: they remove the markup to decide whether there is evidence at
    all, and they accept a file that is attached to the record as the
    evidence. The evidence is not judged for evaluation words — that
    check applies to the measured value of a step.

    The evidence may also be a **file**: the screenshot of the report is
    the evidence. The file is bound to this record through
    ``FileAsset.test_run_case``, not through a link that is parsed out of
    the Markdown — see the comment on that field.
    """

    CONCLUSION_CHOICES = (
        (RUN_CASE_CONCLUSION_PASS, "Pass"),
        (RUN_CASE_CONCLUSION_FAIL, "Fail"),
        (RUN_CASE_CONCLUSION_NA, "Not Applicable"),
        (RUN_CASE_CONCLUSION_MANUAL, "Manual"),
    )

    test_run = models.ForeignKey(
        TestRun,
        on_delete=models.CASCADE,
        related_name="run_cases",
    )
    test_case = models.ForeignKey(
        TestCase,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="run_cases",
    )

    # --- snapshot of the case header ---
    case_number = models.CharField(max_length=32, blank=True, default="")
    case_module_code = models.CharField(max_length=6, blank=True, default="")
    case_title = models.CharField(max_length=255, blank=True, default="")
    case_purpose = models.TextField(blank=True, default="")
    case_object_overview = models.TextField(blank=True, default="")
    case_priority = models.CharField(max_length=2, blank=True, default="")
    case_preconditions = models.TextField(blank=True, default="")
    case_environment = models.TextField(blank=True, default="")

    # --- outcome ---
    conclusion = models.CharField(
        max_length=10,
        choices=CONCLUSION_CHOICES,
        blank=True,
        default="",
    )
    evidence = models.TextField(
        blank=True,
        default="",
        help_text=(
            "The evidence as Markdown source. It is stored as it was typed: "
            "no HTML conversion, no stripping of the markup. A serializer "
            "hands the source out unchanged."
        ),
    )
    remarks = models.TextField(blank=True, default="")
    actual_result_summary = models.TextField(blank=True, default="")

    # --- the two conclusions that need a justification ---
    na_reason = models.TextField(blank=True, default="")
    na_criteria_source = models.TextField(blank=True, default="")
    manual_executor = models.TextField(blank=True, default="")
    manual_condition = models.TextField(blank=True, default="")

    class Meta:
        verbose_name = "Test Run Case"
        verbose_name_plural = "Test Run Cases"
        db_table = "test_run_cases"
        ordering = ("created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["test_run", "test_case"],
                condition=models.Q(test_case__isnull=False, deleted_at__isnull=True),
                name="test_run_case_unique_run_case",
            )
        ]

    def __str__(self):
        return f"{self.case_number} <{self.test_run.number}>"

    @property
    def evidence_attachment_count(self) -> int:
        """The number of files attached to this case record.

        The files are bound by a foreign key (``FileAsset.test_run_case``);
        they are not parsed out of the Markdown source of the evidence. A
        deleted file does not count any more: the evidence of a case may
        be a screenshot, and a deleted screenshot is no evidence.

        The count is a query. A caller that walks many case records
        should fetch the assets in one go (prefetch the ``assets``
        relation) instead of asking every record for its count.
        """
        return self.assets.filter(is_deleted=False).count()

    def save(self, *args, **kwargs):
        self._validate_run_belongs_to_project()
        self._assert_run_not_frozen()
        self._assert_conclusion_is_final()
        self._assert_case_is_not_already_in_the_batch()
        super(TestRunCase, self).save(*args, **kwargs)

    def _assert_case_is_not_already_in_the_batch(self):
        """The same test case may not sit in one batch twice.

        The database carries the same rule as a partial unique constraint
        (``test_run_case_unique_run_case``); the check here turns the
        violation into the stable ``duplicate_case_number`` reason
        instead of letting an ``IntegrityError`` escape. A soft-deleted
        row does not block the case from being added again.
        """
        if not (self.test_run_id and self.test_case_id):
            return

        duplicate = (
            TestRunCase.objects.filter(
                test_run_id=self.test_run_id,
                test_case_id=self.test_case_id,
            )
            .exclude(pk=self.pk)
            .exists()
        )
        if duplicate:
            raise ValidationError(
                {
                    "test_case": (
                        f"{DUPLICATE_CASE_NUMBER}: the test case is already "
                        "in this batch."
                    )
                }
            )

    def _validate_run_belongs_to_project(self):
        if (
            self.test_run_id
            and self.project_id
            and self.test_run.project_id != self.project_id
        ):
            raise ValidationError(
                {"test_run": "The batch must belong to the same project as the case record."}
            )

    def _assert_conclusion_is_final(self):
        """A conclusion that is written into a batch is never changed.

        「结论一经写进批次不再改，重跑开新批次」 (STD-SW-003 §6). Once the
        conclusion is written it may neither be changed nor cleared; a
        wrong conclusion is corrected by voiding the case record and
        running the case again in a new batch.

        The other fields of the record (the evidence, the remarks, the
        summary) stay writable while the batch is open — only the
        conclusion itself is final.
        """
        if self._state.adding:
            return
        stored = (
            TestRunCase.all_objects.filter(pk=self.pk)
            .values_list("conclusion", flat=True)
            .first()
        )
        if not stored:
            return
        if stored != self.conclusion:
            raise ValidationError(
                {
                    "conclusion": (
                        f"{CONCLUSION_IS_FINAL}: the conclusion of a case in a "
                        "batch is never changed; void the record and run the "
                        "case again in a new batch."
                    )
                }
            )

    @classmethod
    def create_for_test_case(cls, test_case: TestCase, test_run: TestRun, **kwargs):
        """Add ``test_case`` to ``test_run`` and snapshot it.

        The header of the case and every row of its step table are copied
        into the batch, so a later edit of the case does not rewrite the
        history of the batch. The copy is one transaction: a failure
        while the step records are written must not leave a case in the
        batch without its snapshot steps.
        """
        if test_case.project_id != test_run.project_id:
            raise ValidationError(
                "A test case can only be added to a batch of the same project."
            )

        with transaction.atomic():
            run_case = cls(
                test_run=test_run,
                test_case=test_case,
                project=test_run.project,
                case_number=test_case.number,
                case_module_code=test_case.module.code,
                case_title=test_case.title,
                case_purpose=test_case.purpose,
                case_object_overview=test_case.object_overview,
                case_priority=test_case.priority,
                case_preconditions=test_case.preconditions,
                case_environment=test_case.environment,
                **kwargs,
            )
            run_case.save()

            for step in test_case.steps.all().order_by("sequence_id"):
                TestRunStepRecord.objects.create(
                    test_run_case=run_case,
                    project=test_run.project,
                    sequence_id=step.sequence_id,
                    action_snapshot=step.action,
                    expected_result_snapshot=step.expected_result,
                )

        return run_case

    def _assert_run_not_frozen(self):
        stored_status = (
            TestRun.all_objects.filter(pk=self.test_run_id)
            .values_list("status", flat=True)
            .first()
        )
        if stored_status == RUN_STATUS_COMPLETED:
            raise ValidationError(
                "A completed batch is frozen; a re-test opens a new batch."
            )


class TestRunStepRecord(ProjectBaseModel):
    """The verdict and the measured value of one step of a batch case.

    Storage contract of ``actual_value``
    ------------------------------------
    ``actual_value`` holds the **Markdown source** of the measured value,
    exactly as it was typed: it is not converted to HTML and the markup
    is not stripped. A serializer hands the source out unchanged — the
    front end renders it. The rules of :mod:`plane.db.testcase_validity`
    read the source: they remove the markup to decide whether a value was
    measured at all, and they judge the evaluation words on the text that
    is left.

    The measured value may also be a **file**: a screenshot of the screen
    is what the tester saw. The file is bound to this record through
    ``FileAsset.test_run_step_record``, not through a link that is parsed
    out of the Markdown — see the comment on that field.
    """

    CONCLUSION_CHOICES = (
        (STEP_VERDICT_PASS, "Pass"),
        (STEP_VERDICT_FAIL, "Fail"),
        (STEP_VERDICT_UNSET, "Not Marked"),
    )

    test_run_case = models.ForeignKey(
        TestRunCase,
        on_delete=models.CASCADE,
        related_name="step_records",
    )
    sequence_id = models.PositiveIntegerField()
    action_snapshot = models.TextField(blank=True, default="")
    expected_result_snapshot = models.TextField(blank=True, default="")
    conclusion = models.CharField(
        max_length=10,
        choices=CONCLUSION_CHOICES,
        default=STEP_VERDICT_UNSET,
    )
    actual_value = models.TextField(
        blank=True,
        default="",
        help_text=(
            "The measured value as Markdown source. It is stored as it was "
            "typed: no HTML conversion, no stripping of the markup. A "
            "serializer hands the source out unchanged."
        ),
    )

    class Meta:
        verbose_name = "Test Run Step Record"
        verbose_name_plural = "Test Run Step Records"
        db_table = "test_run_step_records"
        ordering = ("sequence_id",)
        constraints = [
            models.UniqueConstraint(
                fields=["test_run_case", "sequence_id"],
                name="test_run_step_record_unique_run_case_sequence",
            )
        ]

    def __str__(self):
        return f"{self.test_run_case.case_number}#{self.sequence_id}"

    @property
    def attachment_count(self) -> int:
        """The number of files attached to this step record.

        The files are bound by a foreign key
        (``FileAsset.test_run_step_record``); they are not parsed out of
        the Markdown source of the measured value. A deleted file does not
        count any more: a screenshot of the measured result is a measured
        value, and a deleted screenshot is none.

        The count is a query. A caller that walks many step records
        should fetch the assets in one go (prefetch the ``assets``
        relation) instead of asking every record for its count.
        """
        return self.assets.filter(is_deleted=False).count()

    def save(self, *args, **kwargs):
        self._validate_run_case_belongs_to_project()
        self._assert_run_not_frozen()
        self._assert_verdict_is_final()
        super(TestRunStepRecord, self).save(*args, **kwargs)

    def _validate_run_case_belongs_to_project(self):
        if (
            self.test_run_case_id
            and self.project_id
            and self.test_run_case.project_id != self.project_id
        ):
            raise ValidationError(
                {
                    "test_run_case": "The case record must belong to the same project as the step record."
                }
            )

    def _assert_verdict_is_final(self):
        """A verdict that is marked on a step is never changed.

        The verdict box of a step is a deliberate act (「两框不预勾、不留空」):
        once ✔ or ✘ is marked it may not be changed back or swapped. The
        measured value stays writable — it is the observation, not the
        verdict — and a wrong verdict is corrected by running the case
        again in a new batch.
        """
        if self._state.adding:
            return
        stored = (
            TestRunStepRecord.all_objects.filter(pk=self.pk)
            .values_list("conclusion", flat=True)
            .first()
        )
        if stored not in (STEP_VERDICT_PASS, STEP_VERDICT_FAIL):
            return
        if stored != self.conclusion:
            raise ValidationError(
                {
                    "conclusion": (
                        f"{CONCLUSION_IS_FINAL}: the verdict of a step in a "
                        "batch is never changed; void the record and run the "
                        "case again in a new batch."
                    )
                }
            )

    def _assert_run_not_frozen(self):
        stored_status = (
            TestRun.all_objects.filter(pk=self.test_run_case.test_run_id)
            .values_list("status", flat=True)
            .first()
        )
        if stored_status == RUN_STATUS_COMPLETED:
            raise ValidationError(
                "A completed batch is frozen; a re-test opens a new batch."
            )


class TestCaseIssue(ProjectBaseModel):
    """Link between a test case and a work item."""

    test_case = models.ForeignKey(
        TestCase,
        on_delete=models.CASCADE,
        related_name="issue_links",
    )
    issue = models.ForeignKey(
        "db.Issue",
        on_delete=models.CASCADE,
        related_name="test_case_links",
    )

    class Meta:
        verbose_name = "Test Case Issue"
        verbose_name_plural = "Test Case Issues"
        db_table = "test_case_issues"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["test_case", "issue"],
                name="test_case_issue_unique_case_issue",
            )
        ]

    def __str__(self):
        return f"{self.test_case.number} -> {self.issue_id}"

    def save(self, *args, **kwargs):
        self._validate_issue_belongs_to_project()
        super(TestCaseIssue, self).save(*args, **kwargs)

    def _validate_issue_belongs_to_project(self):
        """A case may only be linked to a work item of the same project.

        Same shape as the four sibling guards of this file: the case, the
        work item and the link itself all live in one project. A link
        across projects would be a cross-project data path the moment an
        API serialises it.
        """
        if not (self.issue_id and self.project_id):
            return

        issue_project_id = self.issue.project_id

        if self.test_case_id and self.test_case.project_id != issue_project_id:
            raise ValidationError(
                {
                    "test_case": "The test case and the work item must belong to the same project."
                }
            )

        if issue_project_id != self.project_id:
            raise ValidationError(
                {"issue": "The work item must belong to the same project as the link."}
            )


# --------------------------------------------------------------------------- #
# Number allocation
# --------------------------------------------------------------------------- #


def allocate_test_case_number(module: TestCaseModule) -> int:
    """Reserve the next test-case number inside ``module``.

    The counter is per project × module. The module row is locked with
    ``select_for_update`` so that two concurrent allocations for the same
    module serialise; the counter row is then read under the same lock and
    bumped. This is the ``IssueSequence`` pattern of
    ``plane/db/models/issue.py`` — the lock target is an existing row, so
    no second concurrency primitive is introduced.

    Returns the reserved sequence number (1-based). The caller turns it
    into ``TC-<CODE>-<NNN>`` with :func:`format_case_number`.
    """
    with transaction.atomic():
        locked_module = TestCaseModule.objects.select_for_update().get(pk=module.pk)

        counter, _ = TestCaseSequence.objects.get_or_create(
            module=locked_module,
            project=locked_module.project,
            workspace=locked_module.workspace,
            defaults={"sequence": 0},
        )
        counter = TestCaseSequence.objects.select_for_update().get(pk=counter.pk)

        next_sequence = counter.sequence + 1
        if next_sequence > CASE_SEQUENCE_MAX:
            raise ValidationError(
                f"The module '{locked_module.code}' has used all "
                f"{CASE_SEQUENCE_MAX} of its numbers."
            )

        counter.sequence = next_sequence
        counter.save()
        return next_sequence


def allocate_test_run_number(project: Project, run_date) -> int:
    """Reserve the next batch number of ``project`` for ``run_date``.

    The counter is per project × date. The project row is the lock target
    (a batch has no parent row of its own), so concurrent allocations of
    the same day serialise on it.

    Returns the reserved sequence number (1-based). The caller turns it
    into ``RUN-<YYYYMMDD>-<NN>`` with :func:`format_run_number`.
    """
    with transaction.atomic():
        Project.objects.select_for_update().get(pk=project.pk)

        counter, _ = TestRunSequence.objects.get_or_create(
            project=project,
            workspace=project.workspace,
            run_date=run_date,
            defaults={"sequence": 0},
        )
        counter = TestRunSequence.objects.select_for_update().get(pk=counter.pk)

        next_sequence = counter.sequence + 1
        if next_sequence > RUN_SEQUENCE_MAX:
            raise ValidationError(
                f"The project '{project.name}' has used all {RUN_SEQUENCE_MAX} "
                f"batch numbers for {run_date}."
            )

        counter.sequence = next_sequence
        counter.save()
        return next_sequence
