// UI-only -- no plan/subscription/billing concept exists on the backend
// (confirmed: no stripe/plan/subscription anywhere in the schema or docs).
// Mock data only until real billing integration lands as its own effort.
export interface MockPlan {
  name: string;
  price: string;
  documentsUsed: number;
  documentsLimit: number | null; // null = unlimited
}
