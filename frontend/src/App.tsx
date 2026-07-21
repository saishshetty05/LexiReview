import { Navigate, Route, Routes } from "react-router-dom";
import { LoginPage } from "./pages/LoginPage";
import { ReviewPage } from "./pages/ReviewPage";
import { UploadPage } from "./pages/UploadPage";

// No route guards: there's no GET /auth/me to check session state without a
// request, so an unauthenticated visit to /upload or /review just gets a 401
// from its first fetch, which those pages catch and redirect to /login from.
export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/upload" element={<UploadPage />} />
      <Route path="/review/:jobId" element={<ReviewPage />} />
      <Route path="/" element={<Navigate to="/login" replace />} />
    </Routes>
  );
}
