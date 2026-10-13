import { Navigate, Route, Routes } from "react-router-dom";

import ProtectedRoute from "@/components/ProtectedRoute";
import AppLayout from "@/layout/AppLayout";
import BookingsPage from "@/pages/BookingsPage";
import ChatPage from "@/pages/ChatPage";
import ConversationDetailPage from "@/pages/ConversationDetailPage";
import ConversationsPage from "@/pages/ConversationsPage";
import DashboardPage from "@/pages/DashboardPage";
import DocumentsPage from "@/pages/DocumentsPage";
import EscalationsPage from "@/pages/EscalationsPage";
import InboxPage from "@/pages/InboxPage";
import LoginPage from "@/pages/LoginPage";
import NotFoundPage from "@/pages/NotFoundPage";
import QAReviewPage from "@/pages/QAReviewPage";
import TasksPage from "@/pages/TasksPage";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />

      <Route
        element={
          <ProtectedRoute>
            <AppLayout />
          </ProtectedRoute>
        }
      >
        <Route path="/" element={<DashboardPage />} />
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/conversations" element={<ConversationsPage />} />
        <Route path="/conversations/:id" element={<ConversationDetailPage />} />
        <Route
          path="/escalations"
          element={
            <ProtectedRoute minimumRole="OPERATOR">
              <EscalationsPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/qa"
          element={
            <ProtectedRoute minimumRole="OPERATOR">
              <QAReviewPage />
            </ProtectedRoute>
          }
        />
        <Route path="/tasks" element={<TasksPage />} />
        <Route path="/documents" element={<DocumentsPage />} />
        <Route path="/inbox" element={<InboxPage />} />
        <Route path="/bookings" element={<BookingsPage />} />
        <Route path="/dashboard" element={<Navigate to="/" replace />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
