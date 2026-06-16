import React from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';

const Navbar = () => {
    const { token, role, logout } = useAuth();
    const navigate = useNavigate();

    return (
        <nav className="navbar">
            <Link to="/" className="navbar-logo">
                <span className="navbar-logo-icon">🎓</span>
                <span className="navbar-logo-text">
                    Campus <span>Knowledge Engine</span>
                </span>
            </Link>

            <div className="nav-links">
                <Link to="/">Home</Link>
                {!token && (
                    <>
                        <Link to="/student/login">Student Login</Link>
                        <Link to="/admin/login">Admin</Link>
                    </>
                )}
                {token && role === 'student' && <Link to="/student/portal">Ask a Question</Link>}
                {token && role === 'admin'   && <Link to="/admin/portal">Dashboard</Link>}
                {token && (
                    <button
                        onClick={() => { logout(); navigate('/'); }}
                        className="btn-secondary"
                        style={{ marginLeft: '0.5rem', color: 'rgba(255,255,255,0.85)', borderColor: 'rgba(255,255,255,0.5)' }}
                    >
                        Logout
                    </button>
                )}
            </div>
        </nav>
    );
};

export default Navbar;
