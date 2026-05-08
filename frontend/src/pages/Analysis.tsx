import { motion } from 'framer-motion';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, BarChart, Bar, PieChart, Pie, Cell } from 'recharts';
import { Activity, CheckCircle, Clock, AlertTriangle } from 'lucide-react';
import { useEffect, useState } from 'react';

interface AnalyticsResponse {
  kpis: {
    totalQueries: number;
    answered: number;
    empty: number;
    errors: number;
    avgResponseMs: number;
  };
  trend: Array<{ name: string; queries: number }>;
  distribution: Array<{ name: string; value: number; color: string }>;
  topics: Array<{ topic: string; count: number }>;
}

interface VoiceAnalyticsResponse {
  kpis: {
    totalCommands: number;
    successful: number;
    ambiguous: number;
    errors: number;
    avgResponseMs: number;
  };
  trend: Array<{ name: string; queries: number }>;
  distribution: Array<{ name: string; value: number; color: string }>;
  topics: Array<{ topic: string; count: number }>;
}

const glassCardClass = 'bg-white/[0.06] backdrop-blur-2xl border border-white/15 shadow-[inset_0_1px_0_rgba(255,255,255,0.22),0_18px_40px_rgba(6,4,20,0.28)]';

export default function Analysis() {
  const [legalData, setLegalData] = useState<AnalyticsResponse | null>(null);
  const [voiceData, setVoiceData] = useState<VoiceAnalyticsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<'legal' | 'voice'>('legal');

  useEffect(() => {
    const loadAnalytics = async () => {
      try {
        await Promise.all([
          fetch('http://localhost:8000/api/legal-analytics/seed', { method: 'POST' }),
          fetch('http://localhost:8000/api/voice-analytics/seed', { method: 'POST' }),
        ]);

        const [legalRes, voiceRes] = await Promise.all([
          fetch('http://localhost:8000/api/legal-analytics'),
          fetch('http://localhost:8000/api/voice-analytics'),
        ]);
        if (!legalRes.ok || !voiceRes.ok) throw new Error('Failed to fetch analytics');

        const [legalPayload, voicePayload] = await Promise.all([legalRes.json(), voiceRes.json()]);
        setLegalData(legalPayload);
        setVoiceData(voicePayload);
      } catch (err) {
        console.error(err);
      } finally {
        setLoading(false);
      }
    };
    loadAnalytics();
  }, []);

  const isLegal = activeTab === 'legal';
  const data = isLegal ? legalData : voiceData;

  const kpis = isLegal
    ? [
        { title: 'Total Queries', value: legalData?.kpis.totalQueries ?? 0, icon: Activity, color: 'text-white' },
        { title: 'Answered', value: legalData?.kpis.answered ?? 0, icon: CheckCircle, color: 'text-green-400' },
        { title: 'Empty', value: legalData?.kpis.empty ?? 0, icon: Clock, color: 'text-[#8a5cff]' },
        { title: 'Errors', value: legalData?.kpis.errors ?? 0, icon: AlertTriangle, color: 'text-red-400' },
      ]
    : [
        { title: 'Total Commands', value: voiceData?.kpis.totalCommands ?? 0, icon: Activity, color: 'text-white' },
        { title: 'Successful', value: voiceData?.kpis.successful ?? 0, icon: CheckCircle, color: 'text-green-400' },
        { title: 'Ambiguous', value: voiceData?.kpis.ambiguous ?? 0, icon: Clock, color: 'text-[#8a5cff]' },
        { title: 'Errors', value: voiceData?.kpis.errors ?? 0, icon: AlertTriangle, color: 'text-red-400' },
      ];

  const formatTopicLabel = (label: string) => {
    if (label.length <= 16) return label;
    return `${label.slice(0, 16)}...`;
  };

  return (
    <div className="min-h-screen pt-24 px-6 pb-12 w-full max-w-6xl mx-auto flex flex-col">
      <motion.div 
        initial={{ opacity: 0, y: -20 }}
        animate={{ opacity: 1, y: 0 }}
        className="mb-8"
      >
        <h1 className="text-4xl font-bold text-white mb-2">Analytics & KPIs</h1>
        <p className="text-[#8a5cff]/80">Separate analytics for legal chatbot and voice task manager</p>
      </motion.div>

      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        className="mb-6 inline-flex gap-2 p-1 rounded-2xl border border-white/10 bg-white/[0.03] backdrop-blur-xl w-fit"
      >
        <button
          onClick={() => setActiveTab('legal')}
          className={`px-4 py-2 rounded-xl text-sm transition-all ${
            isLegal ? 'bg-white/12 text-white border border-white/20' : 'text-white/70 hover:text-white'
          }`}
        >
          Legal Analytics
        </button>
        <button
          onClick={() => setActiveTab('voice')}
          className={`px-4 py-2 rounded-xl text-sm transition-all ${
            !isLegal ? 'bg-white/12 text-white border border-white/20' : 'text-white/70 hover:text-white'
          }`}
        >
          Voice Task Analytics
        </button>
      </motion.div>

      {/* KPI Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-8">
        {kpis.map((kpi, idx) => (
          <motion.div 
            key={kpi.title}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: idx * 0.1 }}
            className={`${glassCardClass} rounded-2xl p-6 flex items-center justify-between`}
          >
            <div>
              <p className="text-white/60 text-sm font-medium mb-1">{kpi.title}</p>
              <h3 className={`text-3xl font-bold ${kpi.color}`}>{loading ? '--' : kpi.value}</h3>
            </div>
            <div className={`p-3 bg-white/5 rounded-xl border border-white/5`}>
              <kpi.icon className={`w-6 h-6 ${kpi.color}`} />
            </div>
          </motion.div>
        ))}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        {/* Line Chart */}
        <motion.div 
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ delay: 0.4 }}
          className={`md:col-span-2 ${glassCardClass} rounded-3xl p-6 h-[400px]`}
        >
          <h3 className="text-white font-medium mb-2">{isLegal ? 'Legal Query Trend' : 'Voice Command Trend'}</h3>
          <p className="text-white/50 text-sm mb-6">Average response time: {loading ? '--' : `${(data as any)?.kpis?.avgResponseMs ?? 0} ms`}</p>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={(data as any)?.trend ?? []}>
              <CartesianGrid strokeDasharray="3 3" stroke="#ffffff15" vertical={false} />
              <XAxis dataKey="name" stroke="#ffffff60" tick={{ fill: '#ffffff60' }} axisLine={false} tickLine={false} />
              <YAxis stroke="#ffffff60" tick={{ fill: '#ffffff60' }} axisLine={false} tickLine={false} />
              <Tooltip 
                contentStyle={{ backgroundColor: '#000000dd', border: '1px solid #ffffff20', borderRadius: '12px', color: '#fff' }}
                itemStyle={{ color: '#8a5cff' }}
              />
              <Line type="monotone" dataKey="queries" stroke="#8a5cff" strokeWidth={3} dot={{ fill: '#8a5cff', r: 4 }} activeDot={{ r: 6, fill: '#fff' }} />
            </LineChart>
          </ResponsiveContainer>
        </motion.div>

        {/* Pie Chart */}
        <motion.div 
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ delay: 0.5 }}
          className={`${glassCardClass} rounded-3xl p-6 h-[400px] flex flex-col`}
        >
          <h3 className="text-white font-medium mb-2">{isLegal ? 'Answer Status Distribution' : 'Command Result Distribution'}</h3>
          <div className="flex-grow flex items-center justify-center -ml-4">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={(data as any)?.distribution ?? []}
                  cx="50%"
                  cy="50%"
                  innerRadius={60}
                  outerRadius={80}
                  paddingAngle={5}
                  dataKey="value"
                  stroke="none"
                >
                  {((data as any)?.distribution ?? []).map((entry: any, index: number) => (
                    <Cell key={`cell-${index}`} fill={entry.color} />
                  ))}
                </Pie>
                <Tooltip 
                  contentStyle={{ backgroundColor: '#000000dd', border: '1px solid #ffffff20', borderRadius: '12px', color: '#fff' }}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <div className="flex justify-center gap-4 mt-4">
            {((data as any)?.distribution ?? []).map((item: any) => (
              <div key={item.name} className="flex items-center gap-2">
                <div className="w-3 h-3 rounded-full" style={{ backgroundColor: item.color }} />
                <span className="text-xs text-white/60">{item.name}</span>
              </div>
            ))}
          </div>
        </motion.div>
      </div>

      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.55 }}
        className={`mt-6 ${glassCardClass} rounded-3xl p-6 h-[320px]`}
      >
        <h3 className="text-white font-medium mb-6">{isLegal ? 'Most Asked Legal Topics' : 'Most Used Voice Actions'}</h3>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={(data as any)?.topics ?? []}>
            <CartesianGrid strokeDasharray="3 3" stroke="#ffffff15" vertical={false} />
            <XAxis
              dataKey="topic"
              stroke="#ffffff60"
              tick={{ fill: '#ffffff60', fontSize: 11 }}
              axisLine={false}
              tickLine={false}
              interval={0}
              angle={0}
              height={42}
              tickMargin={8}
              tickFormatter={formatTopicLabel}
            />
            <YAxis stroke="#ffffff60" tick={{ fill: '#ffffff60' }} axisLine={false} tickLine={false} />
            <Tooltip contentStyle={{ backgroundColor: '#000000dd', border: '1px solid #ffffff20', borderRadius: '12px', color: '#fff' }} />
            <Bar dataKey="count" fill="#00ffd1" radius={[8, 8, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </motion.div>
    </div>
  );
}
