import type { OrgMembership } from "@/types/auth";

/**
 * Keep the browser session aligned with the tenancy invariant of one live
 * membership per user and organization.
 *
 * The API/database enforce that invariant, but normalizing at this boundary
 * also keeps an anomalous response from producing duplicate organization
 * controls (and duplicate React keys) throughout the authenticated UI.
 */
export function uniqueOrgMemberships(orgs: OrgMembership[]): OrgMembership[] {
  const byOrgId = new Map<string, OrgMembership>();

  for (const org of orgs) {
    if (!byOrgId.has(org.id)) {
      byOrgId.set(org.id, org);
    }
  }

  return [...byOrgId.values()];
}
