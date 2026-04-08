import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { StudyCalendar } from "./components/StudyCalendar";
import { UploadSyllabus } from "./components/UploadSyllabus";

const queryClient = new QueryClient();

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <div style={{ display: 'flex', flexDirection: 'column', minHeight: '100vh' }}>
        <header style={{ backgroundColor: '#ffffff', borderBottom: '1px solid #e5e7eb', padding: '1rem 2rem' }}>
          <div style={{ margin: '0 auto', maxWidth: 1120, display: 'flex', alignItems: 'center' }}>
            <h1 style={{ margin: 0, fontSize: '1.5rem', color: '#2563eb', fontWeight: 700 }}>StudySync</h1>
          </div>
        </header>

        <main style={{ flex: 1, margin: "0 auto", width: '100%', maxWidth: 1120, padding: "2rem 1.5rem" }}>
          <div style={{ marginBottom: "2rem" }}>
            <h2 style={{ fontSize: '1.875rem', fontWeight: 600, margin: '0 0 0.5rem 0' }}>Welcome to StudySync</h2>
            <p style={{ color: '#4b5563', margin: 0 }}>Streamline your learning by uploading your syllabus and letting us organize your study schedule.</p>
          </div>

          <div style={{ backgroundColor: '#ffffff', padding: '1.5rem', borderRadius: '0.5rem', boxShadow: '0 1px 3px 0 rgba(0, 0, 0, 0.1)', marginBottom: "2rem" }}>
            <h3 style={{ marginTop: 0, marginBottom: '1rem', fontSize: '1.25rem', fontWeight: 600 }}>1. Upload Your Syllabus</h3>
            <UploadSyllabus />
          </div>

          <div style={{ backgroundColor: '#ffffff', padding: '1.5rem', borderRadius: '0.5rem', boxShadow: '0 1px 3px 0 rgba(0, 0, 0, 0.1)' }}>
            <h3 style={{ marginTop: 0, marginBottom: '1rem', fontSize: '1.25rem', fontWeight: 600 }}>2. View Your Study Plan</h3>
            <StudyCalendar />
          </div>
        </main>
        
        <footer style={{ backgroundColor: '#ffffff', borderTop: '1px solid #e5e7eb', padding: '1.5rem', textAlign: 'center', color: '#6b7280', fontSize: '0.875rem' }}>
          &copy; {new Date().getFullYear()} StudySync. All rights reserved.
        </footer>
      </div>
    </QueryClientProvider>
  );
}
