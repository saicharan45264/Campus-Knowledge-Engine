import React, { useState } from 'react';
import { Search, MapPin, Upload, Camera, ChevronLeft, CheckCircle2, ImageIcon, MessageSquare, Clock, User, Mail, X } from 'lucide-react';

export default function StudentLostAndFound({ onBack, onLogout }) {
  const [activeTab, setActiveTab] = useState('search'); 
  const [reported, setReported] = useState(false);
  
  // Search state
  const [searchQuery, setSearchQuery] = useState('');
  const [searchImage, setSearchImage] = useState(null);
  
  // Is Searched state (Controls whether we are showing recently found defaults or Ranked Search List)
  const [isSearched, setIsSearched] = useState(false);

  // Modal state
  const [selectedItem, setSelectedItem] = useState(null);

  const handleReport = (e) => {
    e.preventDefault();
    setReported(true);
    setTimeout(() => {
      setReported(false);
      setActiveTab('search');
    }, 2500);
  };

  const handleSearch = (e) => {
    e.preventDefault();
    setIsSearched(true);
  };

  const clearSearch = () => {
    setSearchQuery('');
    setSearchImage(null);
    setIsSearched(false);
  };

  const defaultItems = [
    { id: 1, title: 'Black Lenovo Thinkpad', location: 'Library 2nd Floor', date: 'Oct 24, 2026', time: '10:30 AM', image: '💻', reporter: 'Alice Smith', email: 'alice.smith@university.edu', description: 'Found a black Lenovo Thinkpad with a few developer stickers. Left it with the library front desk.' },
    { id: 2, title: 'Dark grey laptop case', location: 'Cafeteria', date: 'Oct 23, 2026', time: '1:15 PM', image: '💼', reporter: 'Bob Jones', email: 'b.jones@university.edu', description: 'Empty dark grey laptop sleeve found on table 4 in the main cafeteria.' },
    { id: 3, title: 'Student ID Card', location: 'CSE Block Room 302', date: 'Oct 23, 2026', time: '4:00 PM', image: '🪪', reporter: 'Charlie Davis', email: 'c.davis@university.edu', description: 'Student ID card for John Doe. Dropped near the podium.' },
    { id: 4, title: 'Calculus Textbook', location: 'Bus Stop', date: 'Oct 21, 2026', time: '8:45 AM', image: '📘', reporter: 'Diana Evans', email: 'diana.e@university.edu', description: 'Volume 3 of the Calculus textbook. Found sitting on the bench at the main campus bus stop.' },
  ];

  const searchedItems = [
    { id: 1, title: 'Black Lenovo Thinkpad', location: 'Library 2nd Floor', date: 'Oct 24, 2026', time: '10:30 AM', image: '💻', similarity: 98, reporter: 'Alice Smith', email: 'alice.smith@university.edu', description: 'Found a black Lenovo Thinkpad with a few developer stickers. Left it with the library front desk.' },
    { id: 2, title: 'Dark grey laptop case', location: 'Cafeteria', date: 'Oct 23, 2026', time: '1:15 PM', image: '💼', similarity: 82, reporter: 'Bob Jones', email: 'b.jones@university.edu', description: 'Empty dark grey laptop sleeve found on table 4 in the main cafeteria.' },
  ];

  const itemsToDisplay = isSearched ? searchedItems : defaultItems;

  return (
    <div className="min-h-screen bg-stone-50">
      {/* Navbar */}
      <nav className="bg-white border-b border-rose-200 px-6 py-4 flex items-center justify-between sticky top-0 z-20 shadow-sm relative overflow-visible">
        <div className="flex items-center gap-4">
          <button onClick={onBack} className="text-stone-500 hover:text-stone-900 p-2 rounded-lg hover:bg-rose-50 transition-colors">
            <ChevronLeft size={20} />
          </button>
          <h1 className="text-xl font-bold text-stone-800">Campus Lost & Found</h1>
        </div>
        <div className="flex gap-2 bg-stone-100 p-1.5 rounded-xl border border-stone-200">
          <button onClick={() => setActiveTab('search')} className={`px-5 py-2 text-sm font-bold rounded-lg transition-all ${activeTab === 'search' ? 'bg-white shadow border border-rose-100 text-rose-600' : 'text-stone-600 hover:text-stone-900 hover:bg-stone-50'}`}>Search Items</button>
          <button onClick={() => setActiveTab('report')} className={`px-5 py-2 text-sm font-bold rounded-lg transition-all ${activeTab === 'report' ? 'bg-white shadow border border-rose-100 text-rose-600' : 'text-stone-600 hover:text-stone-900 hover:bg-stone-50'}`}>Report Found Item</button>
        </div>
      </nav>

      <main className="max-w-7xl mx-auto p-6 mt-4 relative z-10">
        {activeTab === 'search' && (
          <div className="animate-in fade-in space-y-8">
            
            {/* SEARCH BANNER */}
            <div className="bg-gradient-to-br from-rose-600 to-rose-800 p-8 rounded-[2rem] shadow-xl text-white max-w-3xl mx-auto overflow-hidden relative">
               <div className="absolute top-0 right-0 w-80 h-80 bg-white/10 rounded-full mix-blend-overlay filter blur-3xl transform translate-x-1/2 -translate-y-1/2"></div>
               
               <h2 className="text-3xl font-extrabold mb-6 tracking-tight relative z-10 text-center">Find Your Lost Items</h2>
               
               <form onSubmit={handleSearch} className="relative z-10 bg-white p-3 rounded-2xl shadow-xl flex flex-col md:flex-row gap-3 items-center">
                  
                  {/* HUGE Search Input */}
                  <div className="w-full relative flex items-center">
                    <Search className="absolute left-4 top-1/2 transform -translate-y-1/2 text-stone-400" size={20} />
                    <input 
                      type="text" 
                      value={searchQuery}
                      onChange={e => setSearchQuery(e.target.value)}
                      placeholder="Describe the item (e.g. 'blue water bottle')..." 
                      className="w-full pl-12 pr-4 py-4 bg-transparent outline-none font-medium text-stone-700 placeholder:text-stone-400"
                    />
                    
                    {/* Multimodal Photo Button Inside Input Box */}
                    <button type="button" onClick={() => setSearchImage(!searchImage)} className={`absolute right-3 p-2 rounded-xl transition-all ${searchImage ? 'bg-rose-100 text-rose-600' : 'bg-stone-100 text-stone-500 hover:bg-stone-200'}`} title="Upload photo to search (Optional)">
                       <ImageIcon size={20} />
                    </button>
                  </div>
                  
                  {/* Huge Search Button */}
                  <button type="submit" className="w-full md:w-auto px-8 py-4 bg-rose-600 hover:bg-rose-700 text-white font-bold rounded-xl whitespace-nowrap shadow-md transition-all">
                    Search
                  </button>
               </form>
               
               {isSearched && (
                 <div className="mt-4 text-center z-10 relative">
                    <button onClick={clearSearch} className="text-sm font-bold text-rose-100 hover:text-white hover:underline underline-offset-4">
                      Clear Search & View Most Recent
                    </button>
                 </div>
               )}
            </div>

            {/* ITEM DISPLAY GRID SECTION */}
            <div className="space-y-4">
              <div className="flex justify-between items-end border-b border-rose-200 pb-3">
                 <h2 className="text-2xl font-bold text-stone-900">
                   {isSearched ? (
                     <span className="flex items-center gap-2">Search Results <span className="bg-rose-100 text-rose-700 text-xs px-2 py-1 rounded-lg">Ranked List</span></span>
                   ) : 'Most Recently Found Items'}
                 </h2>
              </div>
              
              <div className="grid md:grid-cols-3 lg:grid-cols-4 gap-6">
                {itemsToDisplay.map(item => (
                  <div key={item.id} onClick={() => setSelectedItem(item)} className="cursor-pointer bg-white rounded-[1.5rem] overflow-hidden border border-rose-100 shadow-sm hover:shadow-xl hover:-translate-y-1 transition-all duration-300 group relative">
                    {/* CONDITIONAL SIMILARITY BADGE */}
                    {isSearched && item.similarity && (
                      <div className="absolute top-4 left-4 bg-white/95 backdrop-blur text-sm font-bold px-3 py-1.5 rounded-xl shadow-lg flex items-center gap-1.5 text-rose-700 z-10 border border-rose-100 scale-100 group-hover:scale-105 transition-transform">
                        <CheckCircle2 size={16} className="text-rose-500" />
                        {item.similarity}% Match
                      </div>
                    )}
                    
                    <div className="h-48 bg-stone-100 flex items-center justify-center text-7xl group-hover:scale-110 transition-transform duration-300">
                      {item.image}
                    </div>
                    <div className="p-6 border-t border-rose-50">
                      <h3 className="font-bold text-lg text-stone-800 mb-3">{item.title}</h3>
                      <div className="space-y-2">
                        <p className="text-sm text-stone-600 flex items-center gap-2">
                          <MapPin size={16} className="text-rose-400" /> {item.location}
                        </p>
                      </div>
                      <p className="text-xs text-stone-400 mt-4 font-bold uppercase tracking-widest text-right border-t border-rose-50 pt-4">FOUND ON {item.date}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* --- REPORT FOUND ITEM TAB --- */}
        {activeTab === 'report' && (
          <div className="max-w-2xl mx-auto animate-in fade-in slide-in-from-bottom-4">
            {reported ? (
              <div className="bg-rose-50 text-rose-800 p-8 rounded-3xl flex flex-col items-center text-center border border-rose-200 shadow-sm">
                <CheckCircle2 size={48} className="text-rose-500 mb-4" />
                <h2 className="text-2xl font-bold mb-2">Item Reported Successfully!</h2>
                <p className="text-rose-700">The item has been sent to the Admin Review Queue for validation.</p>
              </div>
            ) : (
              <form onSubmit={handleReport} className="bg-white rounded-[2rem] p-10 border border-rose-100 shadow-xl space-y-8">
                 <div className="grid grid-cols-1 gap-6">
                  <div>
                    <label className="block text-sm font-bold text-stone-700 mb-2">Location Found *</label>
                    <div className="relative">
                      <MapPin className="absolute left-4 top-1/2 transform -translate-y-1/2 text-rose-400" size={18} />
                      <input type="text" required placeholder="e.g. Library 2nd Floor desk" className="w-full pl-12 pr-4 py-3.5 bg-stone-50 border border-rose-200 rounded-xl focus:ring-2 focus:ring-rose-500 outline-none hover:border-rose-300 font-medium text-stone-700 transition-colors" />
                    </div>
                  </div>
                </div>

                <div>
                  <label className="block text-sm font-bold text-stone-700 mb-2">Upload Image (Optional but recommended) <span className="text-xs font-normal text-stone-400 ml-1">(Used for Visual Search)</span></label>
                  <div className="border-2 border-dashed border-rose-300 rounded-2xl p-10 flex flex-col items-center justify-center text-stone-500 hover:bg-rose-50 hover:border-rose-400 transition-colors cursor-pointer bg-stone-50/50">
                    <Camera size={40} className="mb-3 text-rose-400" />
                    <p className="font-bold text-stone-700">Click to upload or drag and drop</p>
                    <p className="text-sm mt-1">PNG, JPG up to 5MB</p>
                  </div>
                </div>

                <div>
                  <label className="block text-sm font-bold text-stone-700 mb-2">Item Description <span className="text-xs font-normal text-stone-400 ml-1">(Optional)</span></label>
                  <textarea rows="3" placeholder="Additional details (e.g. brand, stickers, size) to help with semantic search..." className="w-full px-5 py-4 bg-stone-50 border border-rose-200 rounded-xl focus:ring-2 focus:ring-rose-500 outline-none hover:border-rose-300 font-medium text-stone-700 transition-colors resize-none" />
                </div>
                
                <button type="submit" className="w-full py-4 bg-rose-600 hover:bg-rose-700 text-white rounded-xl font-bold shadow-lg transition-all flex items-center justify-center gap-2 transform hover:-translate-y-0.5 text-lg">
                  <Upload size={20} /> Submit for Admin Review
                </button>
              </form>
            )}
          </div>
        )}
      </main>

      {/* ITEM DETAILS MODAL */}
      {selectedItem && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-stone-900/60 backdrop-blur-sm animate-in fade-in">
          <div className="bg-white rounded-[2rem] shadow-2xl w-full max-w-2xl overflow-hidden relative animate-in zoom-in-95 duration-200 border border-rose-100 flex flex-col md:flex-row">
             <button onClick={() => setSelectedItem(null)} className="absolute top-4 right-4 z-10 p-2 bg-white/50 hover:bg-stone-100 rounded-full text-stone-500 transition-colors">
                <X size={20} />
             </button>
             
             {/* Modal Left / Top Image */}
             <div className="md:w-2/5 bg-stone-100 flex items-center justify-center text-8xl py-12 md:py-0 border-b md:border-b-0 md:border-r border-rose-100">
               {selectedItem.image}
             </div>

             {/* Modal Right Content */}
             <div className="md:w-3/5 p-8 flex flex-col justify-between bg-white">
                <div>
                  <h2 className="text-2xl font-extrabold text-stone-900 mb-4">{selectedItem.title}</h2>
                  
                  <div className="space-y-4 mb-6">
                    <div className="flex items-start gap-3 text-stone-600 text-sm">
                       <MapPin className="text-rose-500 shrink-0 mt-0.5" size={18} />
                       <div>
                         <p className="font-bold text-stone-800">Found Location</p>
                         <p>{selectedItem.location}</p>
                       </div>
                    </div>
                    
                    <div className="flex items-start gap-3 text-stone-600 text-sm">
                       <Clock className="text-rose-500 shrink-0 mt-0.5" size={18} />
                       <div>
                         <p className="font-bold text-stone-800">Date & Time Uploaded</p>
                         <p>{selectedItem.date} at {selectedItem.time}</p>
                       </div>
                    </div>

                    <div className="flex items-start gap-3 text-stone-600 text-sm">
                       <User className="text-rose-500 shrink-0 mt-0.5" size={18} />
                       <div>
                         <p className="font-bold text-stone-800">Reported By</p>
                         <p>{selectedItem.reporter}</p>
                         <p className="text-stone-400 flex items-center gap-1 mt-0.5"><Mail size={12}/> {selectedItem.email}</p>
                       </div>
                    </div>
                  </div>

                  <div className="mb-8">
                     <p className="font-bold text-stone-800 text-sm mb-1">Description</p>
                     <p className="text-stone-600 text-sm leading-relaxed bg-stone-50 p-4 rounded-xl border border-stone-100">{selectedItem.description}</p>
                  </div>
                </div>

                <button className="w-full py-3.5 bg-rose-600 hover:bg-rose-700 text-white rounded-xl font-bold shadow-md transition-all flex items-center justify-center gap-2 transform hover:-translate-y-0.5">
                  <MessageSquare size={18} /> Open Chat with Reporter
                </button>
             </div>
          </div>
        </div>
      )}
    </div>
  );
}
