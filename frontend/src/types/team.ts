// UI-only types -- no organization/team/membership concept exists on the
// backend yet (confirmed: no org_id, no memberships table, nothing in
// CONTRACTS.md or the schema). This page is mock-data-only until that
// lands as its own joint, schema-changing effort.
export type TeamRole = "owner" | "member";

export interface TeamMember {
  id: string;
  email: string;
  role: TeamRole;
  status: "active" | "invited";
}
