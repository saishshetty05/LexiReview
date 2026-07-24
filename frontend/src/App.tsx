import { Navigate, Route, Routes } from "react-router-dom";
import { LandingPage } from "@/pages/marketing/LandingPage";
import { PricingPage } from "@/pages/marketing/PricingPage";
import { LoginPage } from "@/pages/auth/LoginPage";
import { SignupPage } from "@/pages/auth/SignupPage";
import { ReviewPage } from "./pages/ReviewPage";
import { UploadPage } from "./pages/UploadPage";

// /upload and /review/:jobId are still the pre-redesign pages (real, working
// end to end) until their own redesign phases land -- kept mounted so the
// app stays fully functional at every checkpoint of this rebuild, per the
// commercial-UI-redesign plan's build order.
export default function App() {
  return (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/pricing" element={<PricingPage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/signup" element={<SignupPage />} />
      <Route path="/upload" element={<UploadPage />} />
      <Route path="/review/:jobId" element={<ReviewPage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
