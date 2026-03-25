import React, { useState } from 'react';
import { Upload, BarChart3, Database, ChevronLeft, LogOut, FileUp, AlertTriangle } from 'lucide-react';

export default function AdminKnowledgeEngine({ onBack, onLogout }) {
  const [activeTab, setActiveTab] = useState('upload'); // 'upload', 'analytics', 'vectors'

  return (
    <div className="flex h-screen bg-slate-50">
      {/* Sidebar Navigation */}
      <div className="w-64 bg-slate-900 text-slate-300 flex flex-col">
        <div className="p-4 border-b border-slate-800 flex justify-between items-center bg-slate-950">
          <button onClick={onBack} className="text-slate-400 hover:text-white flex items-center gap-1 text-sm font-medium transition-colors">
            <ChevronLeft size={16} /> Gateway
          </button>
          <button onClick={onLogout} className="text-slate-400 hover:text-red-400 transition-colors">
            <LogOut size={16} />
          </button>
        </div>
        
        <div className="p-6">
          <h2 className="text-white font-bold mb-6 flex items-center gap-2">
             Admin Dashboard
          </h2>
          <nav className="space-y-2">
            <button 
              onClick={() => setActiveTab('upload')}
              className={`w-full flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium transition-colors ${activeTab === 'upload' ? 'bg-indigo-600 text-white shadow' : 'hover:bg-slate-800 hover:text-white'}`}
            >
              <Upload size={18} /> Upload Document
            </button>
            <button 
              onClick={() => setActiveTab('analytics')}
              className={`w-full flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium transition-colors ${activeTab === 'analytics' ? 'bg-indigo-600 text-white shadow' : 'hover:bg-slate-800 hover:text-white'}`}
            >
              <BarChart3 size={18} /> Query Analytics
            </button>
            <button 
              onClick={() => setActiveTab('vectors')}
              className={`w-full flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium transition-colors ${activeTab === 'vectors' ? 'bg-indigo-600 text-white shadow' : 'hover:bg-slate-800 hover:text-white'}`}
            >
              <Database size={18} /> Manage Vectors
            </button>
          </nav>
        </div>
      </div>

      {/* Main Area */}
      <div className="flex-1 overflow-y-auto p-8">
        {activeTab === 'upload' && (
          <div className="w-full max-w-6xl mx-auto animate-in fade-in">
            <h1 className="text-2xl font-bold text-slate-900 mb-6 flex items-center gap-2">
              <FileUp className="text-indigo-600" /> Upload Official Documents
            </h1>
            
            <form className="bg-white rounded-2xl shadow-sm border border-slate-200 p-8 space-y-6" onSubmit={e => e.preventDefault()}>
              <div className="grid grid-cols-2 gap-6">
                <div>
                  <label className="block text-sm font-medium text-slate-700 mb-2">Document Type</label>
                  <select className="w-full bg-slate-50 border border-slate-200 rounded-lg px-4 py-2.5 focus:ring-2 focus:ring-indigo-500 outline-none">
                    <option>Syllabus</option>
                    <option>PYQ (Previous Year Question)</option>
                    <option>Timetable</option>
                    <option>Academic Calendar</option>
                    <option>Rulebook</option>
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-slate-700 mb-2">Academic Year</label>
                  <select className="w-full bg-slate-50 border border-slate-200 rounded-lg px-4 py-2.5 focus:ring-2 focus:ring-indigo-500 outline-none">
                    <option>2025-2026</option>
                    <option>2026-2027</option>
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-slate-700 mb-2">Department</label>
                  <input type="text" placeholder="e.g. CSE, ECE..." className="w-full bg-slate-50 border border-slate-200 rounded-lg px-4 py-2.5 focus:ring-2 focus:ring-indigo-500 outline-none" />
                </div>
                <div>
                  <label className="block text-sm font-medium text-slate-700 mb-2">Subject</label>
                  <input type="text" placeholder="e.g. Machine Learning" className="w-full bg-slate-50 border border-slate-200 rounded-lg px-4 py-2.5 focus:ring-2 focus:ring-indigo-500 outline-none" />
                </div>
                <div>
                  <label className="block text-sm font-medium text-slate-700 mb-2">Semester</label>
                  <select className="w-full bg-slate-50 border border-slate-200 rounded-lg px-4 py-2.5 focus:ring-2 focus:ring-indigo-500 outline-none">
                    <option>Sem 1</option> <option>Sem 2</option> <option>Sem 3</option> <option>Sem 4</option>
                    <option>Sem 5</option> <option>Sem 6</option> <option>Sem 7</option> <option>Sem 8</option>
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-slate-700 mb-2">Regulation</label>
                  <select className="w-full bg-slate-50 border border-slate-200 rounded-lg px-4 py-2.5 focus:ring-2 focus:ring-indigo-500 outline-none">
                    <option>R20</option> <option>R22</option> <option>R24</option>
                  </select>
                </div>
              </div>

              {/* Drag and Drop */}
              <div className="mt-8 border-2 border-dashed border-indigo-200 rounded-xl p-10 flex flex-col items-center justify-center text-slate-500 bg-indigo-50/50 hover:bg-indigo-50 cursor-pointer transition-colors">
                 <FileUp size={40} className="text-indigo-400 mb-3" />
                 <p className="font-semibold text-indigo-900 mb-1">Drag and drop PDF here</p>
                 <p className="text-sm">or click to browse files</p>
                 <p className="text-xs text-slate-400 mt-2">Maximum file size 50MB</p>
              </div>

              <div className="flex justify-end pt-4 border-t border-slate-100">
                <button type="submit" className="bg-indigo-600 hover:bg-indigo-700 text-white px-8 py-2.5 rounded-lg shadow font-medium">
                  Process & Upload to Vector DB
                </button>
              </div>
            </form>
          </div>
        )}

        {activeTab === 'analytics' && (
          <div className="animate-in fade-in space-y-8">
            <h1 className="text-2xl font-bold text-slate-900 flex items-center gap-2">
              <BarChart3 className="text-indigo-600" /> Query Analytics
            </h1>

            <div className="grid lg:grid-cols-2 gap-8">
              {/* Unanswered Queries */}
              <div className="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden">
                <div className="p-5 border-b border-slate-100 bg-slate-50/50 flex items-center gap-2">
                  <AlertTriangle size={18} className="text-amber-500" />
                  <h2 className="font-bold text-slate-800">Unanswered Queries</h2>
                </div>
                <div className="p-0">
                  <table className="w-full text-sm text-left text-slate-500">
                    <thead className="bg-slate-50 text-xs uppercase text-slate-700">
                      <tr><th className="px-6 py-3">Query</th><th className="px-6 py-3">Dept</th><th className="px-6 py-3">Date</th></tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      <tr className="hover:bg-slate-50"><td className="px-6 py-4 font-medium text-slate-900">What is the passing criteria for ML lab R24?</td><td className="px-6 py-4">CSE</td><td className="px-6 py-4">Today, 10:23 AM</td></tr>
                      <tr className="hover:bg-slate-50"><td className="px-6 py-4 font-medium text-slate-900">Is the tech fest postponed?</td><td className="px-6 py-4">All</td><td className="px-6 py-4">Yesterday</td></tr>
                      <tr className="hover:bg-slate-50"><td className="px-6 py-4 font-medium text-slate-900">Format for project report Sem 8</td><td className="px-6 py-4">ECE</td><td className="px-6 py-4">Oct 23</td></tr>
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Missing Topics */}
              <div className="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden">
                <div className="p-5 border-b border-slate-100 bg-slate-50/50 flex items-center gap-2">
                  <Database size={18} className="text-indigo-500" />
                  <h2 className="font-bold text-slate-800">High-Frequency Missing Topics</h2>
                </div>
                <div className="p-0">
                  <table className="w-full text-sm text-left text-slate-500">
                    <thead className="bg-slate-50 text-xs uppercase text-slate-700">
                      <tr><th className="px-6 py-3">Topic / Keyword</th><th className="px-6 py-3">Frequency</th><th className="px-6 py-3">Action</th></tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      <tr className="hover:bg-slate-50"><td className="px-6 py-4 font-medium text-slate-900">"Hackathon guidelines"</td><td className="px-6 py-4"><span className="text-red-500 font-bold">142</span></td><td className="px-6 py-4"><button className="text-indigo-600 hover:underline">Upload Doc</button></td></tr>
                      <tr className="hover:bg-slate-50"><td className="px-6 py-4 font-medium text-slate-900">"Mid 2 Syllabus AI"</td><td className="px-6 py-4"><span className="text-red-400 font-bold">89</span></td><td className="px-6 py-4"><button className="text-indigo-600 hover:underline">Upload Doc</button></td></tr>
                      <tr className="hover:bg-slate-50"><td className="px-6 py-4 font-medium text-slate-900">"Library fine policy"</td><td className="px-6 py-4"><span className="text-amber-500 font-bold">45</span></td><td className="px-6 py-4"><button className="text-indigo-600 hover:underline">Upload Doc</button></td></tr>
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          </div>
        )}

         {activeTab === 'vectors' && (
           <div className="animate-in fade-in flex flex-col items-center justify-center py-20 text-slate-400">
             <Database size={48} className="mb-4 text-slate-300" />
             <p>Vector database management controls are active.</p>
           </div>
         )}
      </div>
    </div>
  );
}
