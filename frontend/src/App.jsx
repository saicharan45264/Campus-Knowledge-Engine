import React from 'react';
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import { AuthProvider } from './contexts/AuthContext';
import Navbar from './components/Navbar';
import ProtectedRoute from './components/ProtectedRoute';
import Home from './pages/Home';
import StudentLogin from './pages/StudentLogin';
import AdminLogin from './pages/AdminLogin';
import StudentPortal from './pages/StudentPortal';
import AdminPortal from './pages/AdminPortal';

function App() {
    return (
        <AuthProvider>
            <Router>
                <Navbar />
                <Routes>
                    <Route path="/" element={<Home />} />
                    <Route path="/student/login" element={<StudentLogin />} />
                    <Route path="/admin/login" element={<AdminLogin />} />
                    
                    <Route path="/student/portal" element={
                        <ProtectedRoute requiredRole="student">
                            <StudentPortal />
                        </ProtectedRoute>
                    } />
                    
                    <Route path="/admin/portal" element={
                        <ProtectedRoute requiredRole="admin">
                            <AdminPortal />
                        </ProtectedRoute>
                    } />
                </Routes>
            </Router>
        </AuthProvider>
    );
}

export default App;
