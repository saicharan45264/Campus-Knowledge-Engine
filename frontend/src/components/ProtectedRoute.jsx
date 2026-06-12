import React from 'react';
import { Navigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';

const ProtectedRoute = ({ children, requiredRole }) => {
    const { token, role } = useAuth();

    if (!token) {
        return <Navigate to="/" replace />;
    }

    if (requiredRole && role !== requiredRole) {
        return <Navigate to="/" replace />;
    }

    return children;
};

export default ProtectedRoute;
