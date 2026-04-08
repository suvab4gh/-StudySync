import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { StudyCalendar } from "./components/StudyCalendar";
import { UploadSyllabus } from "./components/UploadSyllabus";

const queryClient = new QueryClient();

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <main style={{ margin: "0 auto", maxWidth: 1120, padding: "1.5rem" }}>
        <h1 style={{ marginBottom: "1rem" }}>StudySync Planner</h1>
        <UploadSyllabus />
        <div style={{ marginTop: "1.25rem" }}>
          <StudyCalendar />
        </div>
      </main>
    </QueryClientProvider>
  );
}
