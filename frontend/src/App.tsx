import { Navigate, Route, Routes } from "react-router-dom";
import { ReviewPage } from "./pages/ReviewPage";

// Walking skeleton: one real route. The root redirect exists only so
// `npm run dev` lands somewhere useful -- there is no job list yet.
const SAMPLE_JOB_ID = "00000000-0000-0000-0000-000000000001";

export default function App() {
  return (
    <Routes>
      <Route path="/review/:jobId" element={<ReviewPage />} />
      <Route path="/" element={<Navigate to={`/review/${SAMPLE_JOB_ID}`} replace />} />
    </Routes>
  );
}
