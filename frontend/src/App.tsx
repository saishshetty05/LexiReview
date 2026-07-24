import { Navigate, Route, Routes } from "react-router-dom";
import { LandingPage } from "@/pages/marketing/LandingPage";
import { PricingPage } from "@/pages/marketing/PricingPage";
import { LoginPage } from "@/pages/auth/LoginPage";
import { SignupPage } from "@/pages/auth/SignupPage";
import { DashboardPage } from "@/pages/dashboard/DashboardPage";
import { UploadPage } from "@/pages/upload/UploadPage";
import { ProtectedRoute } from "@/components/layout/ProtectedRoute";
import { ReviewPage } from "./pages/ReviewPage";

// /review/:jobId is still the pre-redesign page (real, working end to end)
// until its own redesign phase lands -- kept mounted so the app stays fully
// functional at every checkpoint of this rebuild, per the
// commercial-UI-redesign plan's build order.
export default function App() {
  return (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/pricing" element={<PricingPage />} />
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
      <Route path="/review/:jobId" element={<ReviewPage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
