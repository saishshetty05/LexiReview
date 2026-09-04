import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { AdminRoute } from "@/components/layout/AdminRoute";
import { ProtectedRoute } from "@/components/layout/ProtectedRoute";

// Route-level code splitting: each page (and whatever it statically imports
// -- notably ReviewPage -> DocumentViewer -> react-pdf/mammoth, the two
// heaviest deps in the app) gets its own chunk instead of all shipping in
// the initial bundle. Landing/pricing visitors never pay for react-pdf.
const LandingPage = lazy(() => import("@/pages/marketing/LandingPage").then((m) => ({ default: m.LandingPage })));
const LoginPage = lazy(() => import("@/pages/auth/LoginPage").then((m) => ({ default: m.LoginPage })));
const SignupPage = lazy(() => import("@/pages/auth/SignupPage").then((m) => ({ default: m.SignupPage })));
const DashboardPage = lazy(() => import("@/pages/dashboard/DashboardPage").then((m) => ({ default: m.DashboardPage })));
const UploadPage = lazy(() => import("@/pages/upload/UploadPage").then((m) => ({ default: m.UploadPage })));
const ReviewPage = lazy(() => import("@/pages/review/ReviewPage").then((m) => ({ default: m.ReviewPage })));
const AccountSettingsPage = lazy(() =>
  import("@/pages/settings/AccountSettingsPage").then((m) => ({ default: m.AccountSettingsPage })),
);
const TeamSettingsPage = lazy(() =>
  import("@/pages/settings/TeamSettingsPage").then((m) => ({ default: m.TeamSettingsPage })),
);
const BillingSettingsPage = lazy(() =>
  import("@/pages/settings/BillingSettingsPage").then((m) => ({ default: m.BillingSettingsPage })),
);
const AdminUsersPage = lazy(() =>
  import("@/pages/admin/AdminUsersPage").then((m) => ({ default: m.AdminUsersPage })),
);

function RouteFallback() {
  return (
    <div className="flex h-screen w-full items-center justify-center">
      <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
    </div>
  );
}

export default function App() {
  return (
    <Suspense fallback={<RouteFallback />}>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/signup" element={<SignupPage />} />
        <Route
          path="/dashboard"
          element={
            <ProtectedRoute>
              <DashboardPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/upload"
          element={
            <ProtectedRoute>
              <UploadPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/review/:jobId"
          element={
            <ProtectedRoute>
              <ReviewPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/settings"
          element={
            <ProtectedRoute>
              <AccountSettingsPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/settings/team"
          element={
            <ProtectedRoute>
              <TeamSettingsPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/settings/billing"
          element={
            <ProtectedRoute>
              <BillingSettingsPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/admin/users"
          element={
            <AdminRoute>
              <AdminUsersPage />
            </AdminRoute>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  );
}
