/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { set, sortBy } from "lodash-es";
import { action, computed, makeObservable, observable, runInAction } from "mobx";
import { computedFn } from "mobx-utils";
// types
import type { EUserPermissions } from "@plane/constants";
import type {
  IWorkspaceBulkInviteFormData,
  IUserLite,
  IWorkspaceMember,
  IWorkspaceMemberInvitation,
  TDispatchMemberRow,
  TDispatchPermissionsMatrix,
} from "@plane/types";
// services
import { WorkspaceService } from "@/services/workspace.service";
// types
import type { IRouterStore } from "@/store/router.store";
import type { IUserStore } from "@/store/user";
// store
import type { IMemberRootStore } from "../index.ts";
import type { IWorkspaceMemberFiltersStore } from "./workspace-member-filters.store";
import { WorkspaceMemberFiltersStore } from "./workspace-member-filters.store";
import type { RootStore } from "@/store/root.store";

export interface IWorkspaceMembership {
  id: string;
  member: string;
  role: EUserPermissions;
  is_active?: boolean;
}

export interface IWorkspaceMemberStore {
  // observables
  workspaceMemberMap: Record<string, Record<string, IWorkspaceMembership>>;
  workspaceMemberInvitations: Record<string, IWorkspaceMemberInvitation[]>;
  // filters store
  filtersStore: IWorkspaceMemberFiltersStore;
  // computed
  workspaceMemberIds: string[] | null;
  workspaceMemberInvitationIds: string[] | null;
  memberMap: Record<string, IWorkspaceMembership> | null;
  // computed actions
  getWorkspaceMemberIds: (workspaceSlug: string) => string[];
  getFilteredWorkspaceMemberIds: (workspaceSlug: string) => string[];
  getSearchedWorkspaceMemberIds: (searchQuery: string) => string[] | null;
  getSearchedWorkspaceInvitationIds: (searchQuery: string) => string[] | null;
  getWorkspaceMemberDetails: (workspaceMemberId: string) => IWorkspaceMember | null;
  getWorkspaceInvitationDetails: (invitationId: string) => IWorkspaceMemberInvitation | null;
  // dispatch (M10)
  getVisibleMemberRows: (workspaceSlug: string) => TDispatchMemberRow[];
  getSpAssignable: (workspaceSlug: string) => boolean;
  getPermissions: (workspaceSlug: string) => TDispatchPermissionsMatrix;
  // fetch actions
  fetchWorkspaceMembers: (workspaceSlug: string) => Promise<IWorkspaceMember[]>;
  fetchPrincipalDispatch: (workspaceSlug: string) => Promise<void>;
  fetchWorkspaceMemberInvitations: (workspaceSlug: string) => Promise<IWorkspaceMemberInvitation[]>;
  // crud actions
  updateMember: (workspaceSlug: string, userId: string, data: { role: EUserPermissions }) => Promise<void>;
  removeMemberFromWorkspace: (workspaceSlug: string, userId: string) => Promise<void>;
  // invite actions
  inviteMembersToWorkspace: (workspaceSlug: string, data: IWorkspaceBulkInviteFormData) => Promise<void>;
  updateMemberInvitation: (
    workspaceSlug: string,
    invitationId: string,
    data: Partial<IWorkspaceMemberInvitation>
  ) => Promise<void>;
  deleteMemberInvitation: (workspaceSlug: string, invitationId: string) => Promise<void>;
  isUserSuspended: (userId: string, workspaceSlug: string) => boolean;
}

export class WorkspaceMemberStore implements IWorkspaceMemberStore {
  // observables
  workspaceMemberMap: {
    [workspaceSlug: string]: Record<string, IWorkspaceMembership>;
  } = {}; // { workspaceSlug: { userId: userDetails } }
  workspaceMemberInvitations: Record<string, IWorkspaceMemberInvitation[]> = {}; // { workspaceSlug: [invitations] }
  // filters store
  filtersStore: IWorkspaceMemberFiltersStore;
  // stores
  routerStore: IRouterStore;
  userStore: IUserStore;
  memberRoot: IMemberRootStore;
  // services
  workspaceService;

  constructor(_memberRoot: IMemberRootStore, _rootStore: RootStore) {
    makeObservable(this, {
      // observables
      workspaceMemberMap: observable,
      workspaceMemberInvitations: observable,
      // computed
      workspaceMemberIds: computed,
      workspaceMemberInvitationIds: computed,
      memberMap: computed,
      // actions
      fetchWorkspaceMembers: action,
      fetchPrincipalDispatch: action,
      updateMember: action,
      removeMemberFromWorkspace: action,
      fetchWorkspaceMemberInvitations: action,
      updateMemberInvitation: action,
      deleteMemberInvitation: action,
    });
    // initialize filters store
    this.filtersStore = new WorkspaceMemberFiltersStore();
    // root store
    this.routerStore = _rootStore.router;
    this.userStore = _rootStore.user;
    this.memberRoot = _memberRoot;
    // services
    this.workspaceService = new WorkspaceService();
  }

  /**
   * @description get the list of all the user ids of all the members of the current workspace
   *
   * M10 — the visible-member predicate (the legacy `is_bot` / `bot_type`
   * filter that used to live here) is now produced server-side by
   * the principal-dispatch endpoint.  When the dispatch has loaded
   * we honour the server's view verbatim.  Until the dispatch loads
   * (or after a failed fetch) we fall back to the legacy member map;
   * the `/api/workspaces/<slug>/members/` list endpoint does NOT
   * filter bots server-side (review finding F2), so the fallback
   * applies the same humans-only predicate via
   * `_isVisibleHumanFallback`.  Either way, no predicate is
   * recomputed in this store on the primary path.
   */
  get workspaceMemberIds() {
    const workspaceSlug = this.routerStore.workspaceSlug;
    if (!workspaceSlug) return null;

    return this.getWorkspaceMemberIds(workspaceSlug);
  }

  get memberMap() {
    const workspaceSlug = this.routerStore.workspaceSlug;
    if (!workspaceSlug) return null;
    return this.workspaceMemberMap?.[workspaceSlug] ?? {};
  }

  get workspaceMemberInvitationIds() {
    const workspaceSlug = this.routerStore.workspaceSlug;
    if (!workspaceSlug) return null;
    return this.workspaceMemberInvitations?.[workspaceSlug]?.map((inv) => inv.id);
  }

  getWorkspaceMemberIds = computedFn((workspaceSlug: string) => {
    const dispatchRows = this.memberRoot.principalStore.getVisibleMemberRows(workspaceSlug);
    if (dispatchRows.length > 0 || this.memberRoot.principalStore.getDispatch(workspaceSlug) !== null) {
      // Dispatch has loaded (or finished with an explicit empty
      // list) — use the server's filtered, sorted answer verbatim.
      // SPs and bots stay out because the server already filtered
      // them; humans stay in, in the order the API returned.
      const humans = dispatchRows.filter((row) => row.kind === "user").map((row) => row.id);
      return this._sortIdsForCurrentUser(workspaceSlug, humans);
    }

    // Fallback: dispatch hasn't loaded yet (or errored).  The
    // `/api/workspaces/<slug>/members/` list endpoint
    // (`apps/api/plane/app/views/workspace/member.py:49`) does NOT
    // apply a bot filter server-side — see review finding F2.  The
    // picker must not flash bot rows during the loading window or
    // when the dispatch has failed, so apply the same humans-only
    // predicate (`VISIBLE_MEMBER_Q` from
    // `plane.core.authz.visibility`) here.  This is a temporary
    // safety net until M10's scope is widened to the API view.
    const members = Object.values(this.workspaceMemberMap?.[workspaceSlug] ?? {});
    const memberIds = members
      .filter((m) => m.is_active !== false && this._isVisibleHumanFallback(m.member))
      .map((m) => m.member);
    return this._sortIdsForCurrentUser(workspaceSlug, memberIds);
  });

  /**
   * @description get the filtered and sorted list of all the user ids of all the members of the workspace
   * @param workspaceSlug
   */
  getFilteredWorkspaceMemberIds = computedFn((workspaceSlug: string) => {
    // Source the row list from the dispatch when it's loaded; the
    // server already applied the bot/AI filter and the
    // `sp_assignable` toggle.  Fall back to the local map when
    // the dispatch hasn't loaded yet — and apply the same
    // humans-only predicate as the primary path
    // (`_isVisibleHumanFallback`) so the picker / search / filter
    // surfaces stay consistent regardless of which source fed the
    // rows.
    const dispatchRows = this.memberRoot.principalStore.getVisibleMemberRows(workspaceSlug);
    let members: IWorkspaceMembership[];
    if (dispatchRows.length > 0 || this.memberRoot.principalStore.getDispatch(workspaceSlug) !== null) {
      members = this._dispatchRowsToMemberships(workspaceSlug, dispatchRows);
    } else {
      members = Object.values(this.workspaceMemberMap?.[workspaceSlug] ?? {}).filter(
        (m) => m.is_active !== false && this._isVisibleHumanFallback(m.member)
      );
    }

    // Use filters store to get filtered member ids
    const memberIds = this.filtersStore.getFilteredMemberIds(
      members,
      this.memberRoot?.memberMap || {},
      (member) => member.member
    );

    return memberIds;
  });

  /**
   * @description get the list of all the user ids that match the search query of all the members of the current workspace
   * @param searchQuery
   */
  getSearchedWorkspaceMemberIds = computedFn((searchQuery: string) => {
    const workspaceSlug = this.routerStore.workspaceSlug;
    if (!workspaceSlug) return null;
    const filteredMemberIds = this.getFilteredWorkspaceMemberIds(workspaceSlug);
    if (!filteredMemberIds) return null;
    const searchedWorkspaceMemberIds = filteredMemberIds.filter((userId) => {
      const memberDetails = this.getWorkspaceMemberDetails(userId);
      if (!memberDetails) return false;
      const memberSearchQuery = `${memberDetails.member.first_name} ${memberDetails.member.last_name} ${
        memberDetails.member?.display_name
      } ${memberDetails.member.email ?? ""}`;
      return memberSearchQuery.toLowerCase()?.includes(searchQuery.toLowerCase());
    });
    return searchedWorkspaceMemberIds;
  });

  /**
   * @description get the list of all the invitation ids that match the search query of all the member invitations of the current workspace
   * @param searchQuery
   */
  getSearchedWorkspaceInvitationIds = computedFn((searchQuery: string) => {
    const workspaceSlug = this.routerStore.workspaceSlug;
    if (!workspaceSlug) return null;
    const workspaceMemberInvitationIds = this.workspaceMemberInvitationIds;
    if (!workspaceMemberInvitationIds) return null;
    const searchedWorkspaceMemberInvitationIds = workspaceMemberInvitationIds.filter((invitationId) => {
      const invitationDetails = this.getWorkspaceInvitationDetails(invitationId);
      if (!invitationDetails) return false;
      const invitationSearchQuery = `${invitationDetails.email}`;
      return invitationSearchQuery.toLowerCase()?.includes(searchQuery.toLowerCase());
    });
    return searchedWorkspaceMemberInvitationIds;
  });

  /**
   * @description get the details of a workspace member
   * @param userId
   */
  getWorkspaceMemberDetails = computedFn((userId: string) => {
    const workspaceSlug = this.routerStore.workspaceSlug;
    if (!workspaceSlug) return null;
    const workspaceMember = this.workspaceMemberMap?.[workspaceSlug]?.[userId];
    if (!workspaceMember) return null;

    const memberDetails: IWorkspaceMember = {
      id: workspaceMember.id,
      role: workspaceMember.role,
      member: this.memberRoot?.memberMap?.[workspaceMember.member],
      is_active: workspaceMember.is_active,
    };
    return memberDetails;
  });

  /**
   * @description get the details of a workspace member invitation
   * @param workspaceSlug
   * @param memberId
   */
  getWorkspaceInvitationDetails = computedFn((invitationId: string) => {
    const workspaceSlug = this.routerStore.workspaceSlug;
    if (!workspaceSlug) return null;
    const invitationsList = this.workspaceMemberInvitations?.[workspaceSlug];
    if (!invitationsList) return null;

    const invitation = invitationsList.find((inv) => inv.id === invitationId);
    return invitation ?? null;
  });

  // -----------------------------------------------------------------
  // M10 — dispatch endpoint accessors.  The store no longer
  // recomputes visibility; it forwards the dispatch answer to
  // components that need a quick read.
  // -----------------------------------------------------------------

  getVisibleMemberRows = computedFn((workspaceSlug: string) =>
    this.memberRoot.principalStore.getVisibleMemberRows(workspaceSlug)
  );

  getSpAssignable = computedFn((workspaceSlug: string) =>
    this.memberRoot.principalStore.getSpAssignable(workspaceSlug)
  );

  getPermissions = computedFn((workspaceSlug: string) => this.memberRoot.principalStore.getPermissions(workspaceSlug));

  /**
   * @description fetch all the members of a workspace
   * @param workspaceSlug
   */
  fetchWorkspaceMembers = async (workspaceSlug: string) =>
    await this.workspaceService.fetchWorkspaceMembers(workspaceSlug).then((response) => {
      runInAction(() => {
        response.forEach((member) => {
          set(this.memberRoot?.memberMap, member.member.id, { ...member.member, joining_date: member.created_at });
          set(this.workspaceMemberMap, [workspaceSlug, member.member.id], {
            id: member.id,
            member: member.member.id,
            role: member.role,
            is_active: member.is_active,
          });
        });
      });
      return response;
    });

  /**
   * M10 — fetch the unified principal-dispatch payload.  This is
   * the single call the legacy "fetch + filter" path now defers
   * to.  The store triggers it on workspace switch (handled in the
   * member root) and the SP toggle UI invalidates the cache by
   * calling `force: true`. */
  fetchPrincipalDispatch = async (workspaceSlug: string) => {
    if (!workspaceSlug) return;
    await this.memberRoot.principalStore.fetchPrincipalDispatch(workspaceSlug);
  };

  /**
   * @description update the role of a workspace member
   * @param workspaceSlug
   * @param userId
   * @param data
   */
  updateMember = async (workspaceSlug: string, userId: string, data: { role: EUserPermissions }) => {
    const memberDetails = this.getWorkspaceMemberDetails(userId);
    if (!memberDetails) throw new Error("Member not found");
    // original data to revert back in case of error
    const originalProjectMemberData = { ...this.workspaceMemberMap?.[workspaceSlug]?.[userId] };
    try {
      runInAction(() => {
        set(this.workspaceMemberMap, [workspaceSlug, userId, "role"], data.role);
      });
      await this.workspaceService.updateWorkspaceMember(workspaceSlug, memberDetails.id, data);
    } catch (error) {
      // revert back to original members in case of error
      runInAction(() => {
        set(this.workspaceMemberMap, [workspaceSlug, userId], originalProjectMemberData);
      });
      throw error;
    }
  };

  /**
   * @description remove a member from workspace
   * @param workspaceSlug
   * @param userId
   */
  removeMemberFromWorkspace = async (workspaceSlug: string, userId: string) => {
    const memberDetails = this.getWorkspaceMemberDetails(userId);
    if (!memberDetails) throw new Error("Member not found");
    // oxlint-disable-next-line promise/always-return
    await this.workspaceService.deleteWorkspaceMember(workspaceSlug, memberDetails?.id).then(() => {
      runInAction(() => {
        set(this.workspaceMemberMap, [workspaceSlug, userId, "is_active"], false);
      });
    });
  };

  /**
   * @description fetch all the member invitations of a workspace
   * @param workspaceSlug
   */
  fetchWorkspaceMemberInvitations = async (workspaceSlug: string) =>
    await this.workspaceService.workspaceInvitations(workspaceSlug).then((response) => {
      runInAction(() => {
        set(this.workspaceMemberInvitations, workspaceSlug, response);
      });
      return response;
    });

  /**
   * @description bulk invite members to a workspace
   * @param workspaceSlug
   * @param data
   */
  inviteMembersToWorkspace = async (workspaceSlug: string, data: IWorkspaceBulkInviteFormData) => {
    const response = await this.workspaceService.inviteWorkspace(workspaceSlug, data);
    await this.fetchWorkspaceMemberInvitations(workspaceSlug);
    return response;
  };

  /**
   * @description update the role of a member invitation
   * @param workspaceSlug
   * @param invitationId
   * @param data
   */
  updateMemberInvitation = async (
    workspaceSlug: string,
    invitationId: string,
    data: Partial<IWorkspaceMemberInvitation>
  ) => {
    const originalMemberInvitations = [...(this.workspaceMemberInvitations?.[workspaceSlug] ?? [])]; // in case of error, we will revert back to original members
    try {
      const memberInvitations = originalMemberInvitations?.map((invitation) => ({
        ...invitation,
        ...(invitation.id === invitationId && data),
      }));
      // optimistic update
      runInAction(() => {
        set(this.workspaceMemberInvitations, workspaceSlug, memberInvitations);
      });
      await this.workspaceService.updateWorkspaceInvitation(workspaceSlug, invitationId, data);
    } catch (error) {
      // revert back to original members in case of error
      runInAction(() => {
        set(this.workspaceMemberInvitations, workspaceSlug, originalMemberInvitations);
      });
      throw error;
    }
  };

  /**
   * @description delete a member invitation
   * @param workspaceSlug
   * @param memberId
   */
  deleteMemberInvitation = async (workspaceSlug: string, invitationId: string) =>
    // oxlint-disable-next-line promise/always-return
    await this.workspaceService.deleteWorkspaceInvitations(workspaceSlug.toString(), invitationId).then(() => {
      runInAction(() => {
        this.workspaceMemberInvitations[workspaceSlug] = this.workspaceMemberInvitations[workspaceSlug].filter(
          (inv) => inv.id !== invitationId
        );
      });
    });

  isUserSuspended = computedFn((userId: string, workspaceSlug: string) => {
    if (!workspaceSlug) return false;
    const workspaceMember = this.workspaceMemberMap?.[workspaceSlug]?.[userId];
    return workspaceMember?.is_active === false;
  });

  // -----------------------------------------------------------------
  // private helpers
  // -----------------------------------------------------------------

  /** Apply the "current user first, then alphabetical by display
   *  name" sort the legacy code shipped, but over an arbitrary
   *  list of member ids.  Used to keep the picker ordering
   *  identical regardless of which source fed the ids. */
  private _sortIdsForCurrentUser = (workspaceSlug: string, ids: string[]): string[] => {
    const sorted = sortBy(ids, [
      (id) => id !== this.userStore?.data?.id,
      (id) => this.memberRoot?.memberMap?.[id]?.display_name?.toLowerCase(),
    ]);
    return sorted;
  };

  /** Project the dispatch rows back into the membership shape the
   *  filters store consumes, so the filter pipeline keeps working
   *  unchanged.  The dispatch endpoint already gives us the role
   *  per row, so we wrap it in a membership stub. */
  private _dispatchRowsToMemberships = (workspaceSlug: string, rows: TDispatchMemberRow[]): IWorkspaceMembership[] => {
    return rows
      .filter((row) => row.kind === "user")
      .map((row) => ({
        id: row.id,
        member: row.id,
        role: (row.role ?? 0) as EUserPermissions,
        is_active: row.is_active,
      }));
  };

  /** F2 — humans-only predicate for the dispatch-loading fallback
   *  path.  Mirrors ``VISIBLE_MEMBER_Q`` on the server side
   *  (``plane.core.authz.visibility``: ``Q(member__is_bot=False)``).
   *  Used while the dispatch payload is in flight or after a
   *  failed fetch, so the picker / search / filter / mention
   *  surfaces don't render bot rows.  Once the dispatch loads the
   *  primary path takes over and this helper is dormant. */
  private _isVisibleHumanFallback = (memberId: string): boolean => {
    const user = this.memberRoot?.memberMap?.[memberId];
    return !user?.is_bot;
  };
}

// Re-export the IUserLite type alias for downstream consumers
// that destructured the old file.  The shape is unchanged; this
// keeps the public surface stable.
export type { IUserLite };
