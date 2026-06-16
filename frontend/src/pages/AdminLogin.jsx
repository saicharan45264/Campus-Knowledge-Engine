import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import Api from '../services/api';
import { useAuth } from '../contexts/AuthContext';

const AdminLogin = () => {
    const [username, setUsername] = useState('');
    const [password, setPassword] = useState('');
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);
    const navigate = useNavigate();
    const { login } = useAuth();

    const handleSubmit = async (e) => {
        e.preventDefault();
        setError(''); setLoading(true);
        try {
            const response = await Api.adminLogin(username, password);
            login(response.access_token, 'admin');
            navigate('/admin/portal');
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="page center">
            <div className="form-card">
                <div className="form-icon">🛡️</div>
                <div className="form-title">Admin Login</div>
                <div className="form-subtitle">Sign in to access the admin dashboard</div>

                <form onSubmit={handleSubmit}>
                    <div className="form-group">
                        <label>Username</label>
                        <input
                            type="text"
                            placeholder="admin"
                            required
                            value={username}
                            onChange={(e) => setUsername(e.target.value)}
                            id="admin-username"
                        />
                    </div>
                    <div className="form-group">
                        <label>Password</label>
                        <input
                            type="password"
                            placeholder="••••••••"
                            required
                            value={password}
                            onChange={(e) => setPassword(e.target.value)}
                            id="admin-password"
                        />
                    </div>
                    <button
                        type="submit"
                        className="btn-primary full-width"
                        disabled={loading}
                        id="admin-submit-btn"
                    >
                        {loading ? 'Signing in...' : 'Sign In'}
                    </button>
                </form>

                {error && <div className="msg error">{error}</div>}
            </div>
        </div>
    );
};

export default AdminLogin;
