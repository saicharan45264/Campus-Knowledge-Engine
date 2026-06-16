import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import Api from '../services/api';
import { useAuth } from '../contexts/AuthContext';

const DEPARTMENTS = ['CSE', 'ECE', 'EEE', 'ME', 'CIVIL', 'IT', 'AIDS', 'AIML'];
const REGULATION_YEARS = ['2019', '2021', '2023'];

const StudentLogin = () => {
    const [isRegistering, setIsRegistering] = useState(false);
    const [formData, setFormData] = useState({
        name: '', college_mail: '', password: '', department: '',
        semester: '', section_id: '', regulation_year: '', academic_year: ''
    });
    const [error, setError] = useState('');
    const [success, setSuccess] = useState('');
    const [loading, setLoading] = useState(false);

    const navigate = useNavigate();
    const { login } = useAuth();

    const handleChange = (e) => setFormData({ ...formData, [e.target.name]: e.target.value });

    const handleSubmit = async (e) => {
        e.preventDefault();
        setError(''); setSuccess(''); setLoading(true);
        try {
            if (isRegistering) {
                await Api.studentRegister({ ...formData, semester: parseInt(formData.semester) || 1 });
                setSuccess('Registration successful! Please login.');
                setIsRegistering(false);
            } else {
                const response = await Api.studentLogin(formData.college_mail, formData.password);
                login(response.access_token, 'student');
                navigate('/student/portal');
            }
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    const toggle = () => { setIsRegistering(!isRegistering); setError(''); setSuccess(''); };

    return (
        <div className="page center">
            <div className="form-card">
                <div className="form-icon">🎓</div>
                <div className="form-title">{isRegistering ? 'Create Account' : 'Student Login'}</div>
                <div className="form-subtitle">
                    {isRegistering ? 'Register to access the Knowledge Engine' : 'Sign in to your student account'}
                </div>

                <form onSubmit={handleSubmit}>
                    {isRegistering && (
                        <>
                            <div className="form-group">
                                <label>Full Name</label>
                                <input name="name" placeholder="Your full name" required onChange={handleChange} />
                            </div>
                            <div className="form-grid-2">
                                <div className="form-group">
                                    <label>Department</label>
                                    <select name="department" required onChange={handleChange} value={formData.department}>
                                        <option value="">Select</option>
                                        {DEPARTMENTS.map(d => <option key={d}>{d}</option>)}
                                    </select>
                                </div>
                                <div className="form-group">
                                    <label>Semester</label>
                                    <select name="semester" required onChange={handleChange} value={formData.semester}>
                                        <option value="">Select</option>
                                        {[1,2,3,4,5,6,7,8].map(s => <option key={s}>{s}</option>)}
                                    </select>
                                </div>
                            </div>
                            <div className="form-grid-2">
                                <div className="form-group">
                                    <label>Section</label>
                                    <input name="section_id" placeholder="e.g. CSE-F AB3" required onChange={handleChange} />
                                </div>
                                <div className="form-group">
                                    <label>Regulation Year</label>
                                    <select name="regulation_year" required onChange={handleChange} value={formData.regulation_year}>
                                        <option value="">Select</option>
                                        {REGULATION_YEARS.map(r => <option key={r}>{r}</option>)}
                                    </select>
                                </div>
                            </div>
                            <div className="form-group">
                                <label>Academic Year</label>
                                <input name="academic_year" placeholder="e.g. 2023-2024" required onChange={handleChange} />
                            </div>
                        </>
                    )}

                    <div className="form-group">
                        <label>College Email</label>
                        <input type="email" name="college_mail" placeholder="you@cb.amrita.edu" required onChange={handleChange} />
                    </div>
                    <div className="form-group">
                        <label>Password</label>
                        <input type="password" name="password" placeholder="••••••••" required onChange={handleChange} />
                    </div>

                    <button
                        type="submit"
                        className="btn-primary full-width"
                        disabled={loading}
                        id="student-submit-btn"
                    >
                        {loading ? 'Please wait...' : (isRegistering ? 'Create Account' : 'Sign In')}
                    </button>

                    <p className="switch-form">
                        {isRegistering ? 'Already have an account? ' : "Don't have an account? "}
                        <button type="button" className="link-btn" onClick={toggle}>
                            {isRegistering ? 'Sign In' : 'Register'}
                        </button>
                    </p>
                </form>

                {error   && <div className="msg error">{error}</div>}
                {success && <div className="msg success">{success}</div>}
            </div>
        </div>
    );
};

export default StudentLogin;
