import React from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';

const Navbar = () => {
    const { token, role, logout } = useAuth();
    const navigate = useNavigate();

    const handleLogout = () => {
        logout();
        navigate('/');
    };

    return (
        <nav className="navbar">
            <div className="logo"><Link to="/">Miranda</Link></div>
            <div className="nav-links">
                <Link to="/">Home</Link>
                {!token && (
                    <>
                        <Link to="/student/login">Student Portal</Link>
                        <Link to="/admin/login">Admin Portal</Link>
                    </>
                )}
                {token && role === 'student' && (
                    <Link to="/student/portal">Query Engine</Link>
                )}
                {token && role === 'admin' && (
                    <Link to="/admin/portal">Upload Docs</Link>
                )}
                {token && (
                    <button onClick={handleLogout} className="btn-primary" style={{ marginLeft: '2rem' }}>
                        Logout
                    </button>
                )}
            </div>
        </nav>
    );
};

export default Navbar;
