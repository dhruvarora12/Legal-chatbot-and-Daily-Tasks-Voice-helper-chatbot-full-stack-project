import { BrowserRouter, Routes, Route } from 'react-router-dom';
import Layout from './components/Layout';
import Dashboard from './pages/Dashboard';
import VoiceTaskApp from './pages/VoiceTaskApp';
import LegalChatbot from './pages/LegalChatbot';
import Analysis from './pages/Analysis';

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="voice-task" element={<VoiceTaskApp />} />
          <Route path="legal-chat" element={<LegalChatbot />} />
          <Route path="analysis" element={<Analysis />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

export default App;
