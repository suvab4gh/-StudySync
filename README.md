# -StudySync
 AI-powered academic planner that turns messy syllabi into adaptive, stress-optimized study schedules.

## Added Backend Schema

- SQLModel entities:
	- `User`
	- `Course`
	- `SyllabusItem`
	- `StudyBlock`
- Alembic migration for creating all four tables and indexes.

Files:
- `backend/app/models.py`
- `backend/alembic/versions/20260407_0001_create_studysync_tables.py`

## Added Frontend Components

- Upload component with drag/drop, Zod validation, and TanStack Query invalidation for `/upload`:
	- `frontend/src/components/UploadSyllabus.tsx`
- FullCalendar schedule component with color-coded blocks, drag-to-reschedule, and PATCH sync to `/schedule/:id`:
	- `frontend/src/components/StudyCalendar.tsx`
- Shared API helpers:
	- `frontend/src/lib/api.ts`

## Frontend Dependencies

Install these in your frontend app:

```bash
npm install @tanstack/react-query zod @fullcalendar/react @fullcalendar/daygrid @fullcalendar/timegrid @fullcalendar/interaction
```

## Quick Usage

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { UploadSyllabus } from "./components/UploadSyllabus";
import { StudyCalendar } from "./components/StudyCalendar";

const queryClient = new QueryClient();

export function App() {
	return (
		<QueryClientProvider client={queryClient}>
			<UploadSyllabus />
			<StudyCalendar />
		</QueryClientProvider>
	);
}
```
