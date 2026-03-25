import React from 'react';
import { BookOpen, Search, LogOut } from 'lucide-react';

export default function GatewayScreen({ role, onNavigate, onLogout }) {
  return (
    <div className="min-h-screen flex flex-col pt-16 bg-slate-50 relative">
      <button 
        onClick={onLogout}
        className="absolute top-6 right-8 flex items-center gap-2 text-slate-500 hover:text-slate-900 transition-colors bg-white px-4 py-2 rounded-lg shadow-sm font-medium"
      >
        <LogOut size={16} /> Logout
      </button>

      <div className="max-w-5xl mx-auto px-6 w-full mt-12">
        <div className="text-center mb-16">
          <h1 className="text-4xl font-extrabold text-slate-900 tracking-tight">
            Welcome to Campus Hub
          </h1>
          <p className="mt-4 text-lg text-slate-500 max-w-2xl mx-auto">
            Select an application module to continue. You are logged in as {role === 'admin' ? 'an Admin' : 'a Student'}.
          </p>
        </div>

        <div className="grid md:grid-cols-2 gap-8">
          {/* Card 1: Knowledge Engine */}
          <button 
            onClick={() => onNavigate(role === 'admin' ? 'admin-knowledge' : 'student-knowledge')}
            className="group block w-full text-left bg-white rounded-3xl p-10 shadow-sm border border-slate-200 hover:shadow-xl hover:border-indigo-200 transition-all duration-300 transform hover:-translate-y-1"
          >
            <div className="bg-indigo-50 w-20 h-20 rounded-2xl flex items-center justify-center mb-8 group-hover:scale-110 transition-transform duration-300">
              <BookOpen className="text-indigo-600 w-10 h-10" />
            </div>
            <h2 className="text-2xl font-bold text-slate-900 mb-3 group-hover:text-indigo-700 transition-colors">
              Academic Knowledge Engine
            </h2>
            <p className="text-slate-500 leading-relaxed">
              {role === 'admin' 
                ? 'Manage document vectors, view query analytics, and upload official academic materials.'
                : 'Ask academic queries and interact with official university guidelines, pyq, and timetables.'}
            </p>
          </button>

          {/* Card 2: Lost & Found */}
          <button 
            onClick={() => onNavigate(role === 'admin' ? 'admin-lostfound' : 'student-lostfound')}
            className="group block w-full text-left bg-white rounded-3xl p-10 shadow-sm border border-slate-200 hover:shadow-xl hover:border-indigo-200 transition-all duration-300 transform hover:-translate-y-1"
          >
            <div className="bg-slate-100 w-20 h-20 rounded-2xl flex items-center justify-center mb-8 group-hover:scale-110 transition-transform duration-300">
              <Search className="text-slate-700 w-10 h-10" />
            </div>
            <h2 className="text-2xl font-bold text-slate-900 mb-3 group-hover:text-indigo-700 transition-colors">
              Campus Lost & Found
            </h2>
            <p className="text-slate-500 leading-relaxed">
              {role === 'admin'
                ? 'Moderate pending item reports, approve found items, and manage the campus inventory.'
                : 'Report items you have found or search the database for items you have lost on campus.'}
            </p>
          </button>
        </div>
      </div>
    </div>
  );
}
