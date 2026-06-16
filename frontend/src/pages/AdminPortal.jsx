import React, { useState, useEffect } from 'react';
import Api from '../services/api';
import { useAuth } from '../contexts/AuthContext';

const DOC_TYPES = [
    { value: 'curriculum',        label: 'Curriculum' },
    { value: 'timetable',         label: 'Timetable' },
    { value: 'academic_calendar', label: 'Academic Calendar' },
    { value: 'regulations',       label: 'Regulations' },
    { value: 'policy',            label: 'Policy' },
    { value: 'other',             label: 'Other' },
];

const statusClass = (s) => {
    if (!s) return 'pending';
    if (s === 'indexed') return 'indexed';
    if (s.startsWith('error')) return 'error';
    return 'pending';
};

const TABS = [
    { id: 'upload',   icon: '⬆️',  label: 'Upload Document' },
    { id: 'insights', icon: '📊',  label: 'Documents' },
    { id: 'students', icon: '👥',  label: 'Students' },
];

const AdminPortal = () => {
    const [activeTab, setActiveTab] = useState('upload');
    const [file, setFile] = useState(null);
    const [formData, setFormData] = useState({ document_type: 'curriculum', department: '', semester: '', section_id: '', academic_year: '' });
    const [msg, setMsg] = useState({ text: '', type: '' });
    const [uploading, setUploading] = useState(false);
    const { token } = useAuth();

    const [documents, setDocuments] = useState([]);
    const [loadingDocs, setLoadingDocs] = useState(false);
    const [students, setStudents] = useState([]);
    const [loadingStudents, setLoadingStudents] = useState(false);

    const handleChange = (e) => setFormData({ ...formData, [e.target.name]: e.target.value });

    const handleSubmit = async (e) => {
        e.preventDefault();
        if (!file) { setMsg({ text: 'Please select a file.', type: 'error' }); return; }
        const data = new FormData();
        data.append('file', file);
        Object.keys(formData).forEach(k => { if (formData[k]) data.append(k, formData[k]); });
        data.append('regulation_year', '2023-2027');
        setUploading(true); setMsg({ text: '', type: '' });
        try {
            const res = await Api.uploadDocument(data, token);
            setMsg({ text: `Upload successful! Doc ID: ${res.doc_id}`, type: 'success' });
            e.target.reset(); setFile(null);
            setFormData({ document_type: 'curriculum', department: '', semester: '', section_id: '', academic_year: '' });
        } catch (err) {
            setMsg({ text: err.message, type: 'error' });
        } finally { setUploading(false); }
    };

    const fetchDocuments = async () => {
        setLoadingDocs(true);
        try { const r = await Api.getAdminDocuments(token); setDocuments(r.documents || []); }
        catch { setMsg({ text: 'Failed to load documents', type: 'error' }); }
        finally { setLoadingDocs(false); }
    };

    const fetchStudents = async () => {
        setLoadingStudents(true);
        try { const r = await Api.getAdminStudents(token); setStudents(r.students || []); }
        catch { setMsg({ text: 'Failed to load students', type: 'error' }); }
        finally { setLoadingStudents(false); }
    };

    const handleDelete = async (docId) => {
        if (!window.confirm('Delete this document?')) return;
        try {
            await Api.deleteAdminDocument(docId, token);
            setMsg({ text: 'Document deleted.', type: 'success' });
            fetchDocuments();
        } catch (err) { setMsg({ text: err.message, type: 'error' }); }
    };

    useEffect(() => {
        setMsg({ text: '', type: '' });
        if (activeTab === 'insights') fetchDocuments();
        if (activeTab === 'students') fetchStudents();
    }, [activeTab]);

    return (
        <div className="admin-layout">
            {/* Sidebar */}
            <aside className="admin-sidebar">
                <h3>Admin Panel</h3>
                {TABS.map(t => (
                    <button
                        key={t.id}
                        className={`admin-sidebar-btn ${activeTab === t.id ? 'active' : ''}`}
                        onClick={() => setActiveTab(t.id)}
                    >
                        <span>{t.icon}</span> {t.label}
                    </button>
                ))}
            </aside>

            {/* Content */}
            <main className="admin-content">
                {msg.text && (
                    <div className={`msg ${msg.type}`} style={{ marginBottom: '1.25rem' }}>
                        {msg.text}
                    </div>
                )}

                {/* Upload */}
                {activeTab === 'upload' && (
                    <>
                        <h2>Upload Document</h2>
                        <form onSubmit={handleSubmit} className="upload-card">
                            <div
                                className={`file-drop-zone ${file ? 'has-file' : ''}`}
                                onClick={() => document.getElementById('file-input').click()}
                            >
                                <div className="drop-icon">{file ? '✅' : '📄'}</div>
                                {file
                                    ? <div className="file-name">{file.name}</div>
                                    : <p>Click to select a PDF file</p>
                                }
                                <input
                                    id="file-input"
                                    type="file"
                                    accept=".pdf"
                                    style={{ display: 'none' }}
                                    onChange={(e) => setFile(e.target.files[0])}
                                />
                            </div>

                            <div className="form-group">
                                <label>Document Type</label>
                                <select name="document_type" value={formData.document_type} onChange={handleChange} required>
                                    {DOC_TYPES.map(d => <option key={d.value} value={d.value}>{d.label}</option>)}
                                </select>
                            </div>

                            {['curriculum','timetable'].includes(formData.document_type) && (
                                <div className="form-group">
                                    <label>Department</label>
                                    <input name="department" placeholder="e.g. CSE" onChange={handleChange} value={formData.department} required />
                                </div>
                            )}

                            {formData.document_type === 'timetable' && (
                                <div className="form-grid-2">
                                    <div className="form-group">
                                        <label>Section</label>
                                        <input name="section_id" placeholder="e.g. CSE-A" onChange={handleChange} value={formData.section_id} required />
                                    </div>
                                    <div className="form-group">
                                        <label>Semester (optional)</label>
                                        <input type="number" name="semester" placeholder="e.g. 5" onChange={handleChange} value={formData.semester} />
                                    </div>
                                </div>
                            )}

                            <button type="submit" className="btn-primary full-width" disabled={uploading} id="upload-btn">
                                {uploading ? 'Uploading...' : 'Upload Document'}
                            </button>
                        </form>
                    </>
                )}

                {/* Documents */}
                {activeTab === 'insights' && (
                    <>
                        <h2>Uploaded Documents</h2>
                        {loadingDocs ? <div className="spinner" /> : documents.length === 0 ? (
                            <div className="data-table-wrapper">
                                <div className="empty-state">
                                    <span className="empty-icon">📂</span>
                                    No documents uploaded yet.
                                </div>
                            </div>
                        ) : (
                            <div className="data-table-wrapper">
                                <table className="data-table">
                                    <thead>
                                        <tr>
                                            <th>Type</th>
                                            <th>Context</th>
                                            <th>Status</th>
                                            <th>Uploaded At</th>
                                            <th>Action</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {documents.map(doc => (
                                            <tr key={doc.id}>
                                                <td><strong>{doc.document_type}</strong></td>
                                                <td style={{ lineHeight: 1.8 }}>
                                                    {doc.department   && <span style={{ display:'block' }}>Dept: {doc.department}</span>}
                                                    {doc.semester     && <span style={{ display:'block' }}>Sem: {doc.semester}</span>}
                                                    {doc.section_id   && <span style={{ display:'block' }}>Sec: {doc.section_id}</span>}
                                                    {doc.regulation_year && <span style={{ display:'block' }}>Reg: {doc.regulation_year}</span>}
                                                </td>
                                                <td>
                                                    <span className={`status-badge ${statusClass(doc.status)}`}>
                                                        {doc.status}
                                                    </span>
                                                </td>
                                                <td>{new Date(doc.uploaded_at).toLocaleString()}</td>
                                                <td>
                                                    <button className="btn-danger" onClick={() => handleDelete(doc.id)}>
                                                        Delete
                                                    </button>
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </>
                )}

                {/* Students */}
                {activeTab === 'students' && (
                    <>
                        <h2>Registered Students</h2>
                        {loadingStudents ? <div className="spinner" /> : students.length === 0 ? (
                            <div className="data-table-wrapper">
                                <div className="empty-state">
                                    <span className="empty-icon">👤</span>
                                    No students registered yet.
                                </div>
                            </div>
                        ) : (
                            <div className="data-table-wrapper">
                                <table className="data-table">
                                    <thead>
                                        <tr>
                                            <th>Name</th>
                                            <th>Email</th>
                                            <th>Department</th>
                                            <th>Academic Info</th>
                                            <th>Registered</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {students.map(s => (
                                            <tr key={s.id}>
                                                <td><strong>{s.name}</strong></td>
                                                <td>{s.college_mail}</td>
                                                <td><span className="status-badge indexed">{s.department}</span></td>
                                                <td>
                                                    Sem {s.semester} · Sec {s.section_id}<br />
                                                    <span style={{ fontSize:'0.8rem', color:'#888' }}>Reg: {s.regulation_year} · {s.academic_year}</span>
                                                </td>
                                                <td>{new Date(s.created_at).toLocaleDateString()}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </>
                )}
            </main>
        </div>
    );
};

export default AdminPortal;
