import React, { useState, useEffect, useRef } from 'react';
import { 
  Play, Pause, Plus, RefreshCw, Server, Layers, Briefcase, 
  Terminal, ShieldAlert, LogOut, CheckCircle2, XCircle, AlertCircle, Clock,
  ArrowRight, Users, Settings, Activity, Trash2, Calendar
} from 'lucide-react';
import './App.css';

// ----------------- API HELPER -----------------
const API_BASE = '/api';

async function apiRequest(url, method = 'GET', body = null, token = null) {
  const headers = {};
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  
  let options = { method, headers };
  
  if (body) {
    if (body instanceof FormData) {
      options.body = body;
    } else {
      headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body);
    }
  }
  
  const response = await fetch(`${API_BASE}${url}`, options);
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `HTTP error ${response.status}`);
  }
  return response.json();
}

export default function App() {
  // --- Auth State ---
  const [token, setToken] = useState(localStorage.getItem('token'));
  const [user, setUser] = useState(null);
  const [orgs, setOrgs] = useState([]);
  const [selectedOrg, setSelectedOrg] = useState(null);
  const [projects, setProjects] = useState([]);
  const [selectedProject, setSelectedProject] = useState(null);
  
  // --- View State ---
  const [activeTab, setActiveTab] = useState('dashboard'); // dashboard, queues, jobs, workers, templates
  const [authMode, setAuthMode] = useState('login'); // login, register
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [authError, setAuthError] = useState('');
  
  // --- Core Domain State ---
  const [stats, setStats] = useState({
    total_jobs: 0, running_jobs: 0, queued_jobs: 0,
    completed_jobs: 0, failed_jobs: 0, dead_letter_jobs: 0,
    active_workers: 0, total_queues: 0, throughput_per_min: 0
  });
  const [queues, setQueues] = useState([]);
  const [workers, setWorkers] = useState([]);
  const [jobs, setJobs] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [retryPolicies, setRetryPolicies] = useState([]);
  
  // --- Modals State ---
  const [showOrgModal, setShowOrgModal] = useState(false);
  const [newOrgName, setNewOrgName] = useState('');
  const [showProjectModal, setShowProjectModal] = useState(false);
  const [newProjName, setNewProjName] = useState('');
  const [showQueueModal, setShowQueueModal] = useState(false);
  const [newQueue, setNewQueue] = useState({ name: '', priority: 1, max_concurrency: 5, default_retry_policy_id: '' });
  
  const [showJobModal, setShowJobModal] = useState(false);
  const [newJob, setNewJob] = useState({ queue_id: '', type: 'email_send', payload: '{}', priority: 0, run_at: '', retry_policy_id: '', max_attempts: 3, dependencies: '' });
  
  const [showBatchModal, setShowBatchModal] = useState(false);
  const [newBatch, setNewBatch] = useState({ name: '', queue_id: '', type: 'email_send', size: 5, payload_pattern: '{"to_email": "user_{i}@example.com", "subject": "Batch Msg", "body": "Body text"}' });
  
  const [showTemplateModal, setShowTemplateModal] = useState(false);
  const [newTemplate, setNewTemplate] = useState({ queue_id: '', name: '', type: 'email_send', payload: '{}', cron_expression: '*/5 * * * *' });
  
  const [activeExecutionId, setActiveExecutionId] = useState(null);
  const [activeJobId, setActiveJobId] = useState(null);
  const [activeJobLogs, setActiveJobLogs] = useState([]);
  const [activeJobExecutions, setActiveJobExecutions] = useState([]);
  const [showLogsModal, setShowLogsModal] = useState(false);
  
  // --- UI Filter States ---
  const [statusFilter, setStatusFilter] = useState('');
  
  // --- Refs ---
  const wsRef = useRef(null);

  // ----------------- AUTH EFFECTS -----------------
  useEffect(() => {
    if (token) {
      localStorage.setItem('token', token);
      fetchUserData();
    } else {
      localStorage.removeItem('token');
      setUser(null);
    }
  }, [token]);

  const fetchUserData = async () => {
    try {
      const uData = await apiRequest('/auth/me', 'GET', null, token);
      setUser(uData);
      
      const orgList = await apiRequest('/auth/organizations', 'GET', null, token);
      setOrgs(orgList);
      if (orgList.length > 0) {
        setSelectedOrg(orgList[0]);
      }
    } catch (e) {
      handleLogout();
    }
  };

  const handleAuth = async (e) => {
    e.preventDefault();
    setAuthError('');
    try {
      if (authMode === 'register') {
        await apiRequest('/auth/register', 'POST', { email, password });
        setAuthMode('login');
        alert("Registration successful! Please login.");
      } else {
        const data = await apiRequest('/auth/login', 'POST', { email, password });
        setToken(data.access_token);
      }
    } catch (err) {
      setAuthError(err.message);
    }
  };

  const handleLogout = () => {
    setToken(null);
    if (wsRef.current) wsRef.current.close();
  };

  // ----------------- ORG & PROJECT EFFECTS -----------------
  useEffect(() => {
    if (selectedOrg && token) {
      fetchProjects();
    }
  }, [selectedOrg]);

  const fetchProjects = async () => {
    try {
      const projList = await apiRequest(`/projects/org/${selectedOrg.id}`, 'GET', null, token);
      setProjects(projList);
      if (projList.length > 0) {
        setSelectedProject(projList[0]);
      } else {
        setSelectedProject(null);
      }
    } catch (e) {
      console.error(e);
    }
  };

  const handleCreateOrg = async (e) => {
    e.preventDefault();
    try {
      const org = await apiRequest('/auth/organizations', 'POST', { name: newOrgName }, token);
      setOrgs([...orgs, org]);
      setSelectedOrg(org);
      setShowOrgModal(false);
      setNewOrgName('');
    } catch (e) {
      alert(e.message);
    }
  };

  const handleCreateProject = async (e) => {
    e.preventDefault();
    try {
      const proj = await apiRequest('/projects', 'POST', { name: newProjName, org_id: selectedOrg.id }, token);
      setProjects([...projects, proj]);
      setSelectedProject(proj);
      setShowProjectModal(false);
      setNewProjName('');
    } catch (e) {
      alert(e.message);
    }
  };

  // ----------------- WebSocket Live Stats -----------------
  useEffect(() => {
    if (!selectedProject || !token) return;
    
    // Connect WebSocket
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `/ws/stats/ws?project_id=${selectedProject.id}`;
    
    if (wsRef.current) wsRef.current.close();
    
    const ws = new WebSocket(`${protocol}//${window.location.host}${wsUrl}`);
    wsRef.current = ws;
    
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      setStats(data);
    };
    
    ws.onerror = (e) => console.error("WS Error:", e);
    
    return () => {
      ws.close();
    };
  }, [selectedProject, token]);

  // ----------------- DOMAIN FETCHES -----------------
  useEffect(() => {
    if (selectedProject && token) {
      refreshAll();
    }
  }, [selectedProject, activeTab, token]);

  const refreshAll = () => {
    fetchQueues();
    fetchRetryPolicies();
    if (activeTab === 'dashboard') {
      fetchDashboardStats();
      fetchWorkers();
    } else if (activeTab === 'queues') {
      fetchQueues();
    } else if (activeTab === 'jobs') {
      fetchJobs();
    } else if (activeTab === 'workers') {
      fetchWorkers();
    } else if (activeTab === 'templates') {
      fetchTemplates();
    }
  };

  const fetchDashboardStats = async () => {
    try {
      const s = await apiRequest(`/stats?project_id=${selectedProject.id}`, 'GET', null, token);
      setStats(s);
    } catch (e) {}
  };

  const fetchRetryPolicies = async () => {
    try {
      const pols = await apiRequest('/retry-policies', 'GET', null, token);
      setRetryPolicies(pols);
    } catch (e) {}
  };

  const fetchQueues = async () => {
    try {
      const qList = await apiRequest(`/projects/${selectedProject.id}/queues`, 'GET', null, token);
      setQueues(qList);
      if (qList.length > 0 && !newQueue.queue_id) {
        setNewQueue(prev => ({ ...prev, queue_id: qList[0].id }));
        setNewJob(prev => ({ ...prev, queue_id: qList[0].id }));
        setNewBatch(prev => ({ ...prev, queue_id: qList[0].id }));
        setNewTemplate(prev => ({ ...prev, queue_id: qList[0].id }));
      }
    } catch (e) {}
  };

  const fetchWorkers = async () => {
    try {
      const wList = await apiRequest('/workers', 'GET', null, token);
      setWorkers(wList);
    } catch (e) {}
  };

  const fetchJobs = async () => {
    try {
      const filterStr = statusFilter ? `?status_filter=${statusFilter}` : '';
      const jList = await apiRequest(`/projects/${selectedProject.id}/jobs${filterStr}`, 'GET', null, token);
      setJobs(jList);
    } catch (e) {}
  };

  const fetchTemplates = async () => {
    if (queues.length === 0) return;
    try {
      const allTemplates = [];
      for (const q of queues) {
        const qTemps = await apiRequest(`/queues/${q.id}/templates`, 'GET', null, token);
        allTemplates.push(...qTemps);
      }
      setTemplates(allTemplates);
    } catch (e) {}
  };

  // ----------------- QUEUE ACTIONS -----------------
  const handleCreateQueue = async (e) => {
    e.preventDefault();
    try {
      const q = await apiRequest('/queues', 'POST', {
        project_id: selectedProject.id,
        name: newQueue.name,
        priority: newQueue.priority,
        max_concurrency: newQueue.max_concurrency,
        default_retry_policy_id: newQueue.default_retry_policy_id || null
      }, token);
      setQueues([...queues, q]);
      setShowQueueModal(false);
      setNewQueue({ name: '', priority: 1, max_concurrency: 5, default_retry_policy_id: '' });
    } catch (e) {
      alert(e.message);
    }
  };

  const toggleQueuePaused = async (queue) => {
    try {
      const updated = await apiRequest(`/queues/${queue.id}`, 'PUT', { is_paused: !queue.is_paused }, token);
      setQueues(queues.map(q => q.id === queue.id ? updated : q));
    } catch (e) {
      alert(e.message);
    }
  };

  // ----------------- JOB ACTIONS -----------------
  const handleCreateJob = async (e) => {
    e.preventDefault();
    try {
      let parsedPayload = {};
      try {
        parsedPayload = JSON.parse(newJob.payload);
      } catch (err) {
        return alert("Payload must be valid JSON!");
      }
      
      const body = {
        queue_id: newJob.queue_id,
        type: newJob.type,
        payload: parsedPayload,
        priority: parseInt(newJob.priority),
        max_attempts: parseInt(newJob.max_attempts),
        run_at: newJob.run_at ? new Date(newJob.run_at).toISOString() : null,
        retry_policy_id: newJob.retry_policy_id || null,
        dependencies: newJob.dependencies ? newJob.dependencies.split(',').map(s => s.trim()) : null
      };
      
      await apiRequest('/jobs', 'POST', body, token);
      setShowJobModal(false);
      refreshAll();
    } catch (e) {
      alert(e.message);
    }
  };

  const handleCreateBatch = async (e) => {
    e.preventDefault();
    try {
      let payloadPattern = {};
      try {
        payloadPattern = JSON.parse(newBatch.payload_pattern);
      } catch (err) {
        return alert("Payload must be valid JSON!");
      }
      
      // Generate list of N jobs atomically
      const jobsList = [];
      for (let i = 1; i <= newBatch.size; i++) {
        // Substitute variables in payload
        const payloadStr = JSON.stringify(payloadPattern).replace(/{i}/g, i);
        jobsList.push({
          queue_id: newBatch.queue_id,
          type: newBatch.type,
          payload: JSON.parse(payloadStr),
          priority: 0,
          ref_key: `job_${i}`,
          depends_on_refs: i > 1 ? [`job_${i-1}`] : [] // default batch chaining to test DAG!
        });
      }
      
      await apiRequest('/batches', 'POST', {
        project_id: selectedProject.id,
        name: newBatch.name,
        jobs: jobsList
      }, token);
      
      setShowBatchModal(false);
      refreshAll();
    } catch (e) {
      alert(e.message);
    }
  };

  const handleRetryJob = async (jobId) => {
    try {
      await apiRequest(`/jobs/${jobId}/retry`, 'POST', null, token);
      refreshAll();
    } catch (e) {
      alert(e.message);
    }
  };

  const handleViewLogs = async (jobId) => {
    setActiveJobId(jobId);
    try {
      const executions = await apiRequest(`/jobs/${jobId}/executions`, 'GET', null, token);
      setActiveJobExecutions(executions);
      
      if (executions.length > 0) {
        const latestExec = executions[0];
        setActiveExecutionId(latestExec.id);
        const logs = await apiRequest(`/executions/${latestExec.id}/logs`, 'GET', null, token);
        setActiveJobLogs(logs);
      } else {
        setActiveExecutionId(null);
        setActiveJobLogs([]);
      }
      setShowLogsModal(true);
    } catch (e) {
      alert(e.message);
    }
  };

  const handleSelectExecution = async (execId) => {
    setActiveExecutionId(execId);
    try {
      const logs = await apiRequest(`/executions/${execId}/logs`, 'GET', null, token);
      setActiveJobLogs(logs);
    } catch (e) {
      alert(e.message);
    }
  };

  // ----------------- TEMPLATE ACTIONS -----------------
  const handleCreateTemplate = async (e) => {
    e.preventDefault();
    try {
      let parsedPayload = {};
      try {
        parsedPayload = JSON.parse(newTemplate.payload);
      } catch (err) {
        return alert("Payload must be valid JSON!");
      }
      
      await apiRequest('/templates', 'POST', {
        queue_id: newTemplate.queue_id,
        name: newTemplate.name,
        type: newTemplate.type,
        payload: parsedPayload,
        cron_expression: newTemplate.cron_expression
      }, token);
      setShowTemplateModal(false);
      refreshAll();
    } catch (e) {
      alert(e.message);
    }
  };

  const toggleTemplateStatus = async (temp) => {
    const nextStatus = temp.status === 'active' ? 'paused' : 'active';
    try {
      await apiRequest(`/templates/${temp.id}/status?status_val=${nextStatus}`, 'PUT', null, token);
      refreshAll();
    } catch (e) {
      alert(e.message);
    }
  };

  const triggerTemplate = async (tempId) => {
    try {
      await apiRequest(`/templates/${tempId}/trigger`, 'POST', null, token);
      alert("Job triggered immediately successfully!");
      refreshAll();
    } catch (e) {
      alert(e.message);
    }
  };

  const deleteTemplate = async (tempId) => {
    if (!confirm("Are you sure you want to delete this template?")) return;
    try {
      await apiRequest(`/templates/${tempId}`, 'DELETE', null, token);
      refreshAll();
    } catch (e) {
      alert(e.message);
    }
  };

  // ----------------- VIEW RENDERS -----------------
  if (!token) {
    return (
      <div className="auth-container">
        <div className="auth-card glass-panel animate-fade-in">
          <div className="auth-logo">JOB SCHEDULER</div>
          <h3>Distributed Job Scheduler</h3>
          <p style={{ color: 'var(--text-secondary)', marginBottom: '25px', fontSize: '0.9rem' }}>
            Enterprise asynchronous work queue management platform
          </p>
          
          <form onSubmit={handleAuth}>
            <div className="form-group">
              <label className="form-label">Email Address</label>
              <input 
                type="email" 
                className="input-control" 
                required 
                placeholder="you@company.com" 
                value={email}
                onChange={e => setEmail(e.target.value)}
              />
            </div>
            <div className="form-group">
              <label className="form-label">Password</label>
              <input 
                type="password" 
                className="input-control" 
                required 
                placeholder="••••••••" 
                value={password}
                onChange={e => setPassword(e.target.value)}
              />
            </div>
            
            {authError && <p style={{ color: 'var(--accent-red)', fontSize: '0.85rem', marginBottom: '15px' }}>{authError}</p>}
            
            <button type="submit" className="btn btn-primary" style={{ width: '100%', marginBottom: '15px' }}>
              {authMode === 'login' ? 'Sign In' : 'Create Account'}
            </button>
          </form>
          
          <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
            {authMode === 'login' ? "Don't have an account? " : "Already have an account? "}
            <span 
              style={{ color: 'var(--accent-cyan)', cursor: 'pointer', fontWeight: 600 }}
              onClick={() => setAuthMode(authMode === 'login' ? 'register' : 'login')}
            >
              {authMode === 'login' ? 'Sign Up' : 'Log In'}
            </span>
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="dashboard-layout">
      {/* Sidebar Navigation */}
      <div className="sidebar">
        <div className="sidebar-logo">JOB SCHEDULER</div>
        
        {/* Multi-Tenancy Orgs and Projects Select */}
        <div style={{ marginBottom: '30px' }}>
          <div className="form-group" style={{ marginBottom: '12px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <label className="form-label" style={{ fontSize: '0.7rem' }}>Organization</label>
              <Plus size={14} style={{ cursor: 'pointer', color: 'var(--accent-cyan)' }} onClick={() => setShowOrgModal(true)} />
            </div>
            <select 
              className="input-control select-control" 
              style={{ padding: '8px 12px', fontSize: '0.85rem' }}
              value={selectedOrg?.id || ''}
              onChange={e => {
                const org = orgs.find(o => o.id === e.target.value);
                setSelectedOrg(org);
              }}
            >
              {orgs.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}
            </select>
          </div>
          
          <div className="form-group" style={{ marginBottom: '0' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <label className="form-label" style={{ fontSize: '0.7rem' }}>Project</label>
              <Plus size={14} style={{ cursor: 'pointer', color: 'var(--accent-cyan)' }} onClick={() => setShowProjectModal(true)} />
            </div>
            <select 
              className="input-control select-control" 
              style={{ padding: '8px 12px', fontSize: '0.85rem' }}
              value={selectedProject?.id || ''}
              onChange={e => {
                const proj = projects.find(p => p.id === e.target.value);
                setSelectedProject(proj);
              }}
            >
              {projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </div>
        </div>
        
        <ul className="nav-menu">
          <li className={`nav-item ${activeTab === 'dashboard' ? 'active' : ''}`} onClick={() => setActiveTab('dashboard')}>
            <Activity size={18} /> Overview
          </li>
          <li className={`nav-item ${activeTab === 'queues' ? 'active' : ''}`} onClick={() => setActiveTab('queues')}>
            <Layers size={18} /> Queue Manager
          </li>
          <li className={`nav-item ${activeTab === 'jobs' ? 'active' : ''}`} onClick={() => setActiveTab('jobs')}>
            <Briefcase size={18} /> Jobs Explorer
          </li>
          <li className={`nav-item ${activeTab === 'templates' ? 'active' : ''}`} onClick={() => setActiveTab('templates')}>
            <Calendar size={18} /> Recurring (Cron)
          </li>
          <li className={`nav-item ${activeTab === 'workers' ? 'active' : ''}`} onClick={() => setActiveTab('workers')}>
            <Server size={18} /> Workers Host
          </li>
        </ul>
        
        <div className="sidebar-footer">
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '15px' }}>
            <Users size={16} />
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{user?.email}</span>
          </div>
          <button className="btn btn-secondary btn-sm" style={{ width: '100%', gap: '8px' }} onClick={handleLogout}>
            <LogOut size={14} /> Sign Out
          </button>
        </div>
      </div>

      {/* Main Content Area */}
      <div className="main-content">
        {selectedProject ? (
          <>
            {/* Page Header */}
            <div className="top-header">
              <div className="page-title">
                <h1>{activeTab.charAt(0).toUpperCase() + activeTab.slice(1)}</h1>
                <p>Project: {selectedProject.name}</p>
              </div>
              
              <div className="header-actions">
                <button className="btn btn-secondary" onClick={refreshAll}>
                  <RefreshCw size={16} /> Refresh
                </button>
                
                {activeTab === 'queues' && (
                  <button className="btn btn-primary" onClick={() => setShowQueueModal(true)}>
                    <Plus size={16} /> Create Queue
                  </button>
                )}
                {activeTab === 'jobs' && (
                  <div style={{ display: 'flex', gap: '8px' }}>
                    <button className="btn btn-secondary" onClick={() => setShowBatchModal(true)}>
                      <Plus size={16} /> Submit Batch
                    </button>
                    <button className="btn btn-primary" onClick={() => setShowJobModal(true)}>
                      <Plus size={16} /> Submit Job
                    </button>
                  </div>
                )}
                {activeTab === 'templates' && (
                  <button className="btn btn-primary" onClick={() => setShowTemplateModal(true)}>
                    <Plus size={16} /> Create Template
                  </button>
                )}
              </div>
            </div>

            {/* Dashboard Overview Panel */}
            {activeTab === 'dashboard' && (
              <>
                {/* Dashboard Metrics Grid */}
                <div className="metrics-grid">
                  <div className="glass-panel metric-card">
                    <div className="metric-card-content">
                      <span className="metric-label">Active Workers</span>
                      <span className="metric-value">{stats.active_workers}</span>
                      <span className="metric-change change-up"><CheckCircle2 size={12} /> Running</span>
                    </div>
                  </div>
                  <div className="glass-panel metric-card">
                    <div className="metric-card-content">
                      <span className="metric-label">Jobs Claimed/Running</span>
                      <span className="metric-value">{stats.running_jobs}</span>
                      <span className="metric-change"><Clock size={12} /> Executing</span>
                    </div>
                  </div>
                  <div className="glass-panel metric-card">
                    <div className="metric-card-content">
                      <span className="metric-label">Jobs Queued</span>
                      <span className="metric-value">{stats.queued_jobs}</span>
                      <span className="metric-change" style={{ color: 'var(--accent-yellow)' }}><Clock size={12} /> Waiting</span>
                    </div>
                  </div>
                  <div className="glass-panel metric-card">
                    <div className="metric-card-content">
                      <span className="metric-label">Jobs Completed</span>
                      <span className="metric-value" style={{ color: 'var(--accent-green)' }}>{stats.completed_jobs}</span>
                      <span className="metric-change change-up"><CheckCircle2 size={12} /> Success</span>
                    </div>
                  </div>
                  <div className="glass-panel metric-card">
                    <div className="metric-card-content">
                      <span className="metric-label">Failed / Dead Letter</span>
                      <span className="metric-value" style={{ color: 'var(--accent-red)' }}>{stats.failed_jobs} / {stats.dead_letter_jobs}</span>
                      <span className="metric-change change-down"><XCircle size={12} /> Failures</span>
                    </div>
                  </div>
                </div>

                <div className="dashboard-grid">
                  {/* Active Queues Panel */}
                  <div className="glass-panel">
                    <h3>Queue Health Overview</h3>
                    <div className="list-container">
                      {queues.length === 0 ? (
                        <p style={{ color: 'var(--text-muted)' }}>No queues registered. Create a queue to get started.</p>
                      ) : (
                        queues.map(q => (
                          <div key={q.id} className="list-row">
                            <div className="list-details" style={{ flexGrow: 1, marginRight: '20px' }}>
                              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                <span className="list-name">{q.name}</span>
                                <span className={`badge ${q.is_paused ? 'badge-paused' : 'badge-active'}`}>
                                  {q.is_paused ? 'paused' : 'active'}
                                </span>
                              </div>
                              <div className="list-meta">
                                <span>Priority: {q.priority}</span>
                                <span>Max Concurrency: {q.max_concurrency}</span>
                              </div>
                            </div>
                            
                            <div className="list-actions">
                              <button 
                                className={`btn btn-sm ${q.is_paused ? 'btn-primary' : 'btn-secondary'}`}
                                onClick={() => toggleQueuePaused(q)}
                              >
                                {q.is_paused ? <Play size={12} /> : <Pause size={12} />}
                                {q.is_paused ? 'Resume' : 'Pause'}
                              </button>
                            </div>
                          </div>
                        ))
                      )}
                    </div>
                  </div>

                  {/* Active Workers Panel */}
                  <div className="glass-panel">
                    <h3>Active Workers</h3>
                    <div className="list-container">
                      {workers.length === 0 ? (
                        <p style={{ color: 'var(--text-muted)' }}>No workers connected.</p>
                      ) : (
                        workers.map(w => {
                          const lastHeartbeat = new Date(w.last_heartbeat_at);
                          const isOnline = (new Date() - lastHeartbeat) < 30000;
                          return (
                            <div key={w.id} className="list-row" style={{ padding: '10px 14px' }}>
                              <div className="list-details">
                                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                  <span className={`pulse-dot ${isOnline ? 'online' : 'offline'}`}></span>
                                  <span className="list-name" style={{ fontSize: '0.85rem' }}>{w.id}</span>
                                </div>
                                <div className="list-meta" style={{ fontSize: '0.7rem' }}>
                                  <span>Hostname: {w.hostname}</span>
                                  <span>Status: {w.status}</span>
                                </div>
                              </div>
                            </div>
                          );
                        })
                      )}
                    </div>
                  </div>
                </div>
              </>
            )}

            {/* Queue Management Tab */}
            {activeTab === 'queues' && (
              <div className="glass-panel">
                <h3>Queues Configuration</h3>
                <div className="table-responsive" style={{ marginTop: '20px' }}>
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Queue Name</th>
                        <th>Priority</th>
                        <th>Max Concurrency</th>
                        <th>Default Retry Policy</th>
                        <th>Status</th>
                        <th>Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {queues.length === 0 ? (
                        <tr>
                          <td colSpan="6" style={{ textAlign: 'center', color: 'var(--text-muted)' }}>No queues registered.</td>
                        </tr>
                      ) : (
                        queues.map(q => {
                          const pol = retryPolicies.find(p => p.id === q.default_retry_policy_id);
                          return (
                            <tr key={q.id}>
                              <td><strong>{q.name}</strong></td>
                              <td>{q.priority}</td>
                              <td>{q.max_concurrency}</td>
                              <td>{pol ? `${pol.name} (${pol.strategy})` : 'None'}</td>
                              <td>
                                <span className={`badge ${q.is_paused ? 'badge-paused' : 'badge-active'}`}>
                                  {q.is_paused ? 'Paused' : 'Active'}
                                </span>
                              </td>
                              <td>
                                <div style={{ display: 'flex', gap: '8px' }}>
                                  <button className="btn btn-secondary btn-sm" onClick={() => toggleQueuePaused(q)}>
                                    {q.is_paused ? 'Resume' : 'Pause'}
                                  </button>
                                </div>
                              </td>
                            </tr>
                          );
                        })
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* Jobs Explorer Tab */}
            {activeTab === 'jobs' && (
              <div className="glass-panel">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
                  <h3>Jobs List</h3>
                  <div style={{ display: 'flex', gap: '10px' }}>
                    <select 
                      className="input-control select-control" 
                      style={{ padding: '6px 12px', fontSize: '0.8rem', width: '160px' }}
                      value={statusFilter}
                      onChange={e => setStatusFilter(e.target.value)}
                    >
                      <option value="">All Statuses</option>
                      <option value="queued">Queued</option>
                      <option value="claimed">Claimed</option>
                      <option value="running">Running</option>
                      <option value="completed">Completed</option>
                      <option value="failed">Failed</option>
                      <option value="dead_letter">Dead Letter</option>
                    </select>
                    <button className="btn btn-secondary btn-sm" onClick={fetchJobs}>
                      Apply Filters
                    </button>
                  </div>
                </div>

                <div className="table-responsive">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Job Type</th>
                        <th>Queue</th>
                        <th>Attempts</th>
                        <th>Schedule (run_at)</th>
                        <th>Status</th>
                        <th>Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {jobs.length === 0 ? (
                        <tr>
                          <td colSpan="6" style={{ textAlign: 'center', color: 'var(--text-muted)' }}>No jobs found.</td>
                        </tr>
                      ) : (
                        jobs.map(j => {
                          const q = queues.find(queue => queue.id === j.queue_id);
                          return (
                            <tr key={j.id}>
                              <td>
                                <div style={{ display: 'flex', flexDirection: 'column' }}>
                                  <span style={{ fontWeight: 600 }}>{j.type}</span>
                                  <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>ID: {j.id}</span>
                                </div>
                              </td>
                              <td>{q ? q.name : 'Unknown'}</td>
                              <td>{j.attempt_count} / {j.max_attempts}</td>
                              <td>{j.run_at ? new Date(j.run_at).toLocaleString() : 'Immediate'}</td>
                              <td>
                                <span className={`badge badge-${j.status}`}>{j.status}</span>
                              </td>
                              <td>
                                <div style={{ display: 'flex', gap: '8px' }}>
                                  <button className="btn btn-secondary btn-sm" onClick={() => handleViewLogs(j.id)}>
                                    <Terminal size={12} /> Execution Logs
                                  </button>
                                  {(j.status === 'failed' || j.status === 'dead_letter') && (
                                    <button className="btn btn-primary btn-sm" onClick={() => handleRetryJob(j.id)}>
                                      <RefreshCw size={12} /> Retry
                                    </button>
                                  )}
                                </div>
                              </td>
                            </tr>
                          );
                        })
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* Recurring Templates Tab */}
            {activeTab === 'templates' && (
              <div className="glass-panel">
                <h3>Recurring Cron Jobs</h3>
                <div className="table-responsive" style={{ marginTop: '20px' }}>
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Template Name</th>
                        <th>Queue</th>
                        <th>Job Type</th>
                        <th>Cron Expression</th>
                        <th>Next Run</th>
                        <th>Status</th>
                        <th>Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {templates.length === 0 ? (
                        <tr>
                          <td colSpan="7" style={{ textAlign: 'center', color: 'var(--text-muted)' }}>No templates created.</td>
                        </tr>
                      ) : (
                        templates.map(t => {
                          const q = queues.find(queue => queue.id === t.queue_id);
                          return (
                            <tr key={t.id}>
                              <td><strong>{t.name}</strong></td>
                              <td>{q ? q.name : 'Unknown'}</td>
                              <td>{t.type}</td>
                              <td><code>{t.cron_expression}</code></td>
                              <td>{new Date(t.next_run_at).toLocaleString()}</td>
                              <td>
                                <span className={`badge ${t.status === 'active' ? 'badge-active' : 'badge-paused'}`}>
                                  {t.status}
                                </span>
                              </td>
                              <td>
                                <div style={{ display: 'flex', gap: '8px' }}>
                                  <button className="btn btn-secondary btn-sm" onClick={() => toggleTemplateStatus(t)}>
                                    {t.status === 'active' ? 'Pause' : 'Activate'}
                                  </button>
                                  <button className="btn btn-secondary btn-sm" onClick={() => triggerTemplate(t.id)}>
                                    Trigger Now
                                  </button>
                                  <button className="btn btn-danger btn-sm" onClick={() => deleteTemplate(t.id)}>
                                    <Trash2 size={12} />
                                  </button>
                                </div>
                              </td>
                            </tr>
                          );
                        })
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* Workers Tab */}
            {activeTab === 'workers' && (
              <div className="glass-panel">
                <h3>Workers Directory</h3>
                <div className="table-responsive" style={{ marginTop: '20px' }}>
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Worker ID</th>
                        <th>Hostname</th>
                        <th>Status</th>
                        <th>Last Heartbeat</th>
                        <th>Registered At</th>
                      </tr>
                    </thead>
                    <tbody>
                      {workers.length === 0 ? (
                        <tr>
                          <td colSpan="5" style={{ textAlign: 'center', color: 'var(--text-muted)' }}>No active workers registered.</td>
                        </tr>
                      ) : (
                        workers.map(w => {
                          const lastHeartbeat = new Date(w.last_heartbeat_at);
                          const isOnline = (new Date() - lastHeartbeat) < 30000;
                          return (
                            <tr key={w.id}>
                              <td>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                  <span className={`pulse-dot ${isOnline ? 'online' : 'offline'}`}></span>
                                  <strong>{w.id}</strong>
                                </div>
                              </td>
                              <td>{w.hostname}</td>
                              <td>
                                <span className={`badge ${isOnline ? `badge-${w.status}` : 'badge-offline'}`}>
                                  {isOnline ? w.status : 'offline'}
                                </span>
                              </td>
                              <td>{lastHeartbeat.toLocaleString()}</td>
                              <td>{new Date(w.registered_at).toLocaleString()}</td>
                            </tr>
                          );
                        })
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', minHeight: '60vh' }}>
            <ShieldAlert size={48} style={{ color: 'var(--accent-purple)', marginBottom: '15px' }} />
            <h3>No Projects Available</h3>
            <p style={{ color: 'var(--text-secondary)', marginTop: '8px' }}>Please create a Project in your Sidebar to get started.</p>
            <button className="btn btn-primary" style={{ marginTop: '20px' }} onClick={() => setShowProjectModal(true)}>
              Create Project
            </button>
          </div>
        )}
      </div>

      {/* ----------------- MODALS ----------------- */}

      {/* Org Modal */}
      {showOrgModal && (
        <div className="modal-backdrop">
          <div className="modal-content glass-panel">
            <div className="modal-header">
              <h3>Create Organization</h3>
              <button className="modal-close" onClick={() => setShowOrgModal(false)}>Close</button>
            </div>
            <form onSubmit={handleCreateOrg}>
              <div className="form-group">
                <label className="form-label">Organization Name</label>
                <input 
                  type="text" 
                  className="input-control" 
                  required 
                  placeholder="e.g. My Enterprise" 
                  value={newOrgName}
                  onChange={e => setNewOrgName(e.target.value)}
                />
              </div>
              <div className="modal-footer">
                <button type="button" className="btn btn-secondary" onClick={() => setShowOrgModal(false)}>Cancel</button>
                <button type="submit" className="btn btn-primary">Create</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Project Modal */}
      {showProjectModal && (
        <div className="modal-backdrop">
          <div className="modal-content glass-panel">
            <div className="modal-header">
              <h3>Create Project</h3>
              <button className="modal-close" onClick={() => setShowProjectModal(false)}>Close</button>
            </div>
            <form onSubmit={handleCreateProject}>
              <div className="form-group">
                <label className="form-label">Project Name</label>
                <input 
                  type="text" 
                  className="input-control" 
                  required 
                  placeholder="e.g. Data Pipelines" 
                  value={newProjName}
                  onChange={e => setNewProjName(e.target.value)}
                />
              </div>
              <div className="modal-footer">
                <button type="button" className="btn btn-secondary" onClick={() => setShowProjectModal(false)}>Cancel</button>
                <button type="submit" className="btn btn-primary">Create</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Queue Modal */}
      {showQueueModal && (
        <div className="modal-backdrop">
          <div className="modal-content glass-panel">
            <div className="modal-header">
              <h3>Create Queue</h3>
              <button className="modal-close" onClick={() => setShowQueueModal(false)}>Close</button>
            </div>
            <form onSubmit={handleCreateQueue}>
              <div className="form-group">
                <label className="form-label">Queue Name</label>
                <input 
                  type="text" 
                  className="input-control" 
                  required 
                  placeholder="e.g. mail-queue" 
                  value={newQueue.name}
                  onChange={e => setNewQueue({ ...newQueue, name: e.target.value })}
                />
              </div>
              <div className="form-group">
                <label className="form-label">Priority</label>
                <input 
                  type="number" 
                  className="input-control" 
                  required 
                  value={newQueue.priority}
                  onChange={e => setNewQueue({ ...newQueue, priority: parseInt(e.target.value) })}
                />
              </div>
              <div className="form-group">
                <label className="form-label">Max Concurrency</label>
                <input 
                  type="number" 
                  className="input-control" 
                  required 
                  value={newQueue.max_concurrency}
                  onChange={e => setNewQueue({ ...newQueue, max_concurrency: parseInt(e.target.value) })}
                />
              </div>
              <div className="form-group">
                <label className="form-label">Default Retry Policy</label>
                <select 
                  className="input-control select-control" 
                  value={newQueue.default_retry_policy_id}
                  onChange={e => setNewQueue({ ...newQueue, default_retry_policy_id: e.target.value })}
                >
                  <option value="">None (No retries)</option>
                  {retryPolicies.map(p => <option key={p.id} value={p.id}>{p.name} ({p.strategy})</option>)}
                </select>
              </div>
              <div className="modal-footer">
                <button type="button" className="btn btn-secondary" onClick={() => setShowQueueModal(false)}>Cancel</button>
                <button type="submit" className="btn btn-primary">Create</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Job Modal */}
      {showJobModal && (
        <div className="modal-backdrop">
          <div className="modal-content glass-panel">
            <div className="modal-header">
              <h3>Submit Asynchronous Job</h3>
              <button className="modal-close" onClick={() => setShowJobModal(false)}>Close</button>
            </div>
            <form onSubmit={handleCreateJob}>
              <div className="form-group">
                <label className="form-label">Queue</label>
                <select 
                  className="input-control select-control" 
                  required
                  value={newJob.queue_id}
                  onChange={e => setNewJob({ ...newJob, queue_id: e.target.value })}
                >
                  {queues.map(q => <option key={q.id} value={q.id}>{q.name}</option>)}
                </select>
              </div>
              <div className="form-group">
                <label className="form-label">Job Type</label>
                <select 
                  className="input-control select-control" 
                  required
                  value={newJob.type}
                  onChange={e => setNewJob({ ...newJob, type: e.target.value })}
                >
                  <option value="email_send">Send Email (email_send)</option>
                  <option value="report_generation">Generate Report (report_generation)</option>
                  <option value="data_sync">Sync Database (data_sync)</option>
                  <option value="http_request">Outgoing HTTP Call (http_request)</option>
                </select>
              </div>
              <div className="form-group">
                <label className="form-label">JSON Payload</label>
                <textarea 
                  className="input-control" 
                  style={{ fontFamily: 'monospace', minHeight: '80px', resize: 'vertical' }}
                  required
                  value={newJob.payload}
                  onChange={e => setNewJob({ ...newJob, payload: e.target.value })}
                  placeholder='e.g. {"to_email": "user@test.com", "subject": "Hello", "body": "World"}'
                />
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '15px' }}>
                <div className="form-group">
                  <label className="form-label">Priority</label>
                  <input 
                    type="number" 
                    className="input-control" 
                    value={newJob.priority}
                    onChange={e => setNewJob({ ...newJob, priority: e.target.value })}
                  />
                </div>
                <div className="form-group">
                  <label className="form-label">Max Attempts</label>
                  <input 
                    type="number" 
                    className="input-control" 
                    value={newJob.max_attempts}
                    onChange={e => setNewJob({ ...newJob, max_attempts: e.target.value })}
                  />
                </div>
              </div>
              <div className="form-group">
                <label className="form-label">Specific Retry Policy (Override)</label>
                <select 
                  className="input-control select-control" 
                  value={newJob.retry_policy_id}
                  onChange={e => setNewJob({ ...newJob, retry_policy_id: e.target.value })}
                >
                  <option value="">None (Use default Queue policy)</option>
                  {retryPolicies.map(p => <option key={p.id} value={p.id}>{p.name} ({p.strategy})</option>)}
                </select>
              </div>
              <div className="form-group">
                <label className="form-label">Dependencies (Optional UUID list, comma separated)</label>
                <input 
                  type="text" 
                  className="input-control" 
                  placeholder="e.g. 33333333-3333-3333-3333-333333333333" 
                  value={newJob.dependencies}
                  onChange={e => setNewJob({ ...newJob, dependencies: e.target.value })}
                />
              </div>
              <div className="form-group">
                <label className="form-label">Future Run Schedule (Optional Date-Time)</label>
                <input 
                  type="datetime-local" 
                  className="input-control" 
                  value={newJob.run_at}
                  onChange={e => setNewJob({ ...newJob, run_at: e.target.value })}
                />
              </div>
              <div className="modal-footer">
                <button type="button" className="btn btn-secondary" onClick={() => setShowJobModal(false)}>Cancel</button>
                <button type="submit" className="btn btn-primary">Submit</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Batch Modal */}
      {showBatchModal && (
        <div className="modal-backdrop">
          <div className="modal-content glass-panel">
            <div className="modal-header">
              <h3>Submit Job Batch</h3>
              <button className="modal-close" onClick={() => setShowBatchModal(false)}>Close</button>
            </div>
            <form onSubmit={handleCreateBatch}>
              <div className="form-group">
                <label className="form-label">Batch Name</label>
                <input 
                  type="text" 
                  className="input-control" 
                  required
                  placeholder="e.g. Nightly Sync Batch" 
                  value={newBatch.name}
                  onChange={e => setNewBatch({ ...newBatch, name: e.target.value })}
                />
              </div>
              <div className="form-group">
                <label className="form-label">Queue</label>
                <select 
                  className="input-control select-control" 
                  required
                  value={newBatch.queue_id}
                  onChange={e => setNewBatch({ ...newBatch, queue_id: e.target.value })}
                >
                  {queues.map(q => <option key={q.id} value={q.id}>{q.name}</option>)}
                </select>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: '15px' }}>
                <div className="form-group">
                  <label className="form-label">Jobs Type</label>
                  <select 
                    className="input-control select-control" 
                    required
                    value={newBatch.type}
                    onChange={e => setNewBatch({ ...newBatch, type: e.target.value })}
                  >
                    <option value="email_send">Send Email (email_send)</option>
                    <option value="report_generation">Generate Report (report_generation)</option>
                    <option value="data_sync">Sync Database (data_sync)</option>
                  </select>
                </div>
                <div className="form-group">
                  <label className="form-label">Batch Size (N)</label>
                  <input 
                    type="number" 
                    className="input-control" 
                    required
                    min="1" max="100"
                    value={newBatch.size}
                    onChange={e => setNewBatch({ ...newBatch, size: parseInt(e.target.value) })}
                  />
                </div>
              </div>
              <div className="form-group">
                <label className="form-label">JSON Payload Pattern (Supports '{'{i}'}' index substitution)</label>
                <textarea 
                  className="input-control" 
                  style={{ fontFamily: 'monospace', minHeight: '80px', resize: 'vertical' }}
                  required
                  value={newBatch.payload_pattern}
                  onChange={e => setNewBatch({ ...newBatch, payload_pattern: e.target.value })}
                />
              </div>
              <div className="modal-footer">
                <button type="button" className="btn btn-secondary" onClick={() => setShowBatchModal(false)}>Cancel</button>
                <button type="submit" className="btn btn-primary">Submit Batch</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Template Modal */}
      {showTemplateModal && (
        <div className="modal-backdrop">
          <div className="modal-content glass-panel">
            <div className="modal-header">
              <h3>Create Recurring Cron Template</h3>
              <button className="modal-close" onClick={() => setShowTemplateModal(false)}>Close</button>
            </div>
            <form onSubmit={handleCreateTemplate}>
              <div className="form-group">
                <label className="form-label">Template Name</label>
                <input 
                  type="text" 
                  className="input-control" 
                  required
                  placeholder="e.g. Hourly DB Sync" 
                  value={newTemplate.name}
                  onChange={e => setNewTemplate({ ...newTemplate, name: e.target.value })}
                />
              </div>
              <div className="form-group">
                <label className="form-label">Queue</label>
                <select 
                  className="input-control select-control" 
                  required
                  value={newTemplate.queue_id}
                  onChange={e => setNewTemplate({ ...newTemplate, queue_id: e.target.value })}
                >
                  {queues.map(q => <option key={q.id} value={q.id}>{q.name}</option>)}
                </select>
              </div>
              <div className="form-group">
                <label className="form-label">Job Type</label>
                <select 
                  className="input-control select-control" 
                  required
                  value={newTemplate.type}
                  onChange={e => setNewTemplate({ ...newTemplate, type: e.target.value })}
                >
                  <option value="email_send">Send Email (email_send)</option>
                  <option value="report_generation">Generate Report (report_generation)</option>
                  <option value="data_sync">Sync Database (data_sync)</option>
                </select>
              </div>
              <div className="form-group">
                <label className="form-label">JSON Payload</label>
                <textarea 
                  className="input-control" 
                  style={{ fontFamily: 'monospace', minHeight: '80px', resize: 'vertical' }}
                  required
                  value={newTemplate.payload}
                  onChange={e => setNewTemplate({ ...newTemplate, payload: e.target.value })}
                  placeholder='e.g. {"to_email": "admin@company.com", "subject": "Daily Report", "body": "Compiled contents"}'
                />
              </div>
              <div className="form-group">
                <label className="form-label">Cron Expression</label>
                <input 
                  type="text" 
                  className="input-control" 
                  required
                  placeholder="*/5 * * * * (Every 5 minutes)" 
                  value={newTemplate.cron_expression}
                  onChange={e => setNewTemplate({ ...newTemplate, cron_expression: e.target.value })}
                />
              </div>
              <div className="modal-footer">
                <button type="button" className="btn btn-secondary" onClick={() => setShowTemplateModal(false)}>Cancel</button>
                <button type="submit" className="btn btn-primary">Create Template</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Logs Modal */}
      {showLogsModal && (
        <div className="modal-backdrop">
          <div className="modal-content glass-panel" style={{ maxWidth: '640px' }}>
            <div className="modal-header">
              <div>
                <h3>Execution Terminal</h3>
                <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Job ID: {activeJobId}</span>
              </div>
              <button className="modal-close" onClick={() => setShowLogsModal(false)}>Close</button>
            </div>
            
            {activeJobExecutions.length === 0 ? (
              <p style={{ color: 'var(--text-muted)', textAlign: 'center', padding: '20px' }}>No execution attempts recorded yet.</p>
            ) : (
              <>
                <div className="form-group" style={{ marginBottom: '15px' }}>
                  <label className="form-label" style={{ fontSize: '0.7rem' }}>Select Attempt / Run</label>
                  <select 
                    className="input-control select-control" 
                    style={{ padding: '6px 12px', fontSize: '0.8rem' }}
                    value={activeExecutionId || ''}
                    onChange={e => handleSelectExecution(e.target.value)}
                  >
                    {activeJobExecutions.map(ex => (
                      <option key={ex.id} value={ex.id}>
                        Run Attempt {ex.attempt_number} (Status: {ex.status}) - {new Date(ex.started_at).toLocaleTimeString()}
                      </option>
                    ))}
                  </select>
                </div>
                
                <div className="log-console">
                  {activeJobLogs.length === 0 ? (
                    <span className="log-line"><span className="log-msg">Initializing terminal context... No log messages yet.</span></span>
                  ) : (
                    activeJobLogs.map(l => (
                      <span key={l.id} className="log-line">
                        <span className="log-time">[{new Date(l.timestamp).toLocaleTimeString()}]</span>
                        <span className={`log-level-${l.level}`}>[{l.level.toUpperCase()}]</span>
                        <span className="log-msg">{l.message}</span>
                      </span>
                    ))
                  )}
                </div>
              </>
            )}
            <div className="modal-footer" style={{ marginTop: '15px' }}>
              <button className="btn btn-secondary" onClick={() => setShowLogsModal(false)}>Close Console</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
