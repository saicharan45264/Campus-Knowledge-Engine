import React from 'react';
import { Search, MapPin, Check, X, Edit3, ChevronLeft, LogOut, Building, ShieldAlert } from 'lucide-react';

export default function AdminLostAndFound({ onBack, onLogout }) {
  const pendingItems = [
    { id: 101, title: 'Gold watch with metal strap', location: 'ECE Block Lab 3', uni: 'State University', reporter: 's.johnson@student.edu', date: '2 hours ago', status: 'pending', image: '⌚️' },
    { id: 102, title: '', location: 'Cafeteria Table 4', uni: 'Tech Institute', reporter: 'm.smith@student.edu', date: '4 hours ago', status: 'pending', image: '🎒' },
    { id: 103, title: 'White earbud case, slight scratch', location: 'Library Ground Floor', uni: 'State University', reporter: 'a.williams@student.edu', date: '1 day ago', status: 'pending', image: '🎧' },
  ];

  return (
    <div className="min-h-screen bg-stone-50">
      {/* Navbar */}
      <nav className="bg-stone-900 border-b border-stone-800 text-stone-300 px-6 py-4 flex items-center justify-between sticky top-0 z-10">
        <div className="flex items-center gap-4">
          <button onClick={onBack} className="text-stone-400 hover:text-white p-2 rounded-lg hover:bg-stone-800 transition-colors">
            <ChevronLeft size={20} />
          </button>
          <div className="flex items-center gap-2">
            <ShieldAlert size={20} className="text-rose-500" />
            <h1 className="text-xl font-bold text-white">Validation & Moderation</h1>
          </div>
        </div>
        <div>
          <button onClick={onLogout} className="text-stone-400 hover:text-rose-400 font-medium flex gap-2 items-center">
             <LogOut size={18} /> Logout
          </button>
        </div>
      </nav>

      <main className="max-w-7xl mx-auto p-6 mt-4">
        <div className="mb-8 flex justify-between items-end border-b border-rose-200 pb-4">
          <div>
             <div className="flex items-center gap-2 mb-1">
               <span className="bg-rose-100 text-rose-800 text-[10px] uppercase font-bold tracking-wider px-2 py-0.5 rounded">Phase 2: Control Layer</span>
             </div>
            <h2 className="text-2xl font-bold text-stone-900">Admin Review Queue</h2>
            <p className="text-stone-500 mt-1 max-w-2xl text-sm">Review pending student uploads to maintain data quality and prevent spam. **Only approved items** will generate multimodal embeddings and move to the Vector Database.</p>
          </div>
          <div className="bg-rose-100 text-rose-800 px-4 py-1.5 rounded-xl font-bold text-sm shadow-sm border border-rose-200">
            {pendingItems.length} Pending
          </div>
        </div>

        <div className="grid lg:grid-cols-3 md:grid-cols-2 gap-6">
          {pendingItems.map(item => (
            <div key={item.id} className="bg-white rounded-2xl overflow-hidden shadow-sm border border-rose-200 flex flex-col hover:border-rose-400 transition-colors">
              {/* Image Area */}
              <div className="h-48 bg-stone-100 flex items-center justify-center text-7xl relative">
                {item.image}
              </div>
              
              {/* Content Area */}
              <div className="p-5 flex-1 space-y-3">
                <div className="flex justify-between items-start">
                   <h3 className="font-bold text-lg text-stone-900">
                     {item.title ? item.title : <span className="italic text-stone-400 text-base font-medium">No description provided</span>}
                   </h3>
                </div>
                
                <div className="bg-stone-50 rounded-xl p-3 border border-rose-100 space-y-2">
                  <p className="text-xs text-stone-600 flex items-center gap-2">
                     <Building size={14} className="text-rose-400" /> <span className="font-semibold text-stone-800">{item.uni}</span>
                  </p>
                  <p className="text-xs text-stone-600 flex items-center gap-2">
                     <MapPin size={14} className="text-rose-400" /> {item.location}
                  </p>
                </div>
                
                <div className="pt-2 flex justify-between items-center text-xs text-stone-500">
                  <span className="flex items-center gap-1"><Search size={12}/> {item.reporter}</span>
                  <span>{item.date}</span>
                </div>
              </div>

              {/* Action Buttons */}
              <div className="p-3 border-t border-rose-100 bg-stone-50/50 flex gap-2">
                <button className="flex-1 bg-rose-600 hover:bg-rose-700 text-white py-2.5 rounded-xl text-sm font-bold flex items-center justify-center gap-1.5 transition-colors shadow-sm cursor-pointer border border-transparent">
                  <Check size={16} /> Approve
                </button>
                <button className="flex-1 bg-white border border-rose-200 hover:bg-rose-50 text-stone-700 py-2.5 rounded-xl text-sm font-bold flex items-center justify-center gap-1.5 transition-colors shadow-[0_1px_2px_rgba(225,29,72,0.05)]">
                  <Edit3 size={16} /> Edit
                </button>
                <button className="flex-none px-3 bg-white border border-rose-200 hover:bg-rose-100 hover:text-rose-700 hover:border-rose-300 text-stone-600 rounded-xl font-bold flex items-center justify-center transition-colors shadow-sm">
                  <X size={18} />
                </button>
              </div>
            </div>
          ))}
        </div>
      </main>
    </div>
  );
}
