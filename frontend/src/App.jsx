import { useEffect, useState } from "react";

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";
const PROMPT_GUARD_TERMS = [
  "ignore previous instructions",
  "system prompt",
  "developer instructions",
  "reveal hidden prompt"
];
const ESCALATION_L1_THRESHOLD = 0.7;
const ESCALATION_L2_THRESHOLD = 0.6;

const resolutionSamples = [
  "The app becomes very slow and eventually stops responding. What could be causing this and how should we fix it?",
  "Remote workers cannot connect to the company network even though their credentials are correct. What should we check?",
  "Users are seeing timeout errors when they open or update records. What is the likely issue and resolution?"
];

const triageSamples = [
  {
    ticketSummary: "Customer-facing web application is intermittently unavailable during business hours and multiple teams are blocked.",
    category: "incident",
    ciCategory: "application",
    ciSubcategory: "Web Based Application"
  },
  {
    ticketSummary: "A single user reported a desktop software issue with a workaround available, but it should be checked today.",
    category: "incident",
    ciCategory: "application",
    ciSubcategory: "Desktop Application"
  }
];

const routingSamples = [
  {
    description: "A phone support ticket reports a recurring web application issue with medium impact and medium urgency.",
    category: "Category 55",
    subcategory: "Subcategory 170",
    uSymptom: "Symptom 72",
    impact: "2 - Medium",
    urgency: "2 - Medium",
    contactType: "Phone",
    location: "Location 143"
  },
  {
    description: "Users are calling about a repeated issue tied to a known category and subcategory, and the case should be routed quickly.",
    category: "Category 26",
    subcategory: "Subcategory 174",
    uSymptom: "Symptom 491",
    impact: "2 - Medium",
    urgency: "2 - Medium",
    contactType: "Phone",
    location: ""
  }
];

function validateFreeText(value, label) {
  const trimmed = value.trim().replace(/\s+/g, " ");
  const alphaTokens = trimmed.match(/[a-zA-Z]{2,}/g) || [];
  const lower = trimmed.toLowerCase();

  if (!trimmed) {
    return `${label} cannot be empty.`;
  }
  if (alphaTokens.length < 3) {
    return `${label} needs a more specific issue description.`;
  }
  if (PROMPT_GUARD_TERMS.some((term) => lower.includes(term))) {
    return `${label} contains unsupported prompt-instruction text.`;
  }
  if (/(.)\1{7,}/.test(trimmed)) {
    return `${label} contains repetitive text and should be cleaned up.`;
  }
  return "";
}

function App() {
  const [mode, setMode] = useState("resolution");
  const [resolutionQuery, setResolutionQuery] = useState(resolutionSamples[0]);
  const [resolutionResult, setResolutionResult] = useState(null);
  const [triageForm, setTriageForm] = useState(triageSamples[0]);
  const [triageResult, setTriageResult] = useState(null);
  const [routingForm, setRoutingForm] = useState(routingSamples[0]);
  const [routingResult, setRoutingResult] = useState(null);
  const [stats, setStats] = useState(null);
  const [triageFilters, setTriageFilters] = useState({ category: [], ci_category: [], ci_subcategory: [] });
  const [routingFilters, setRoutingFilters] = useState({
    category: [],
    subcategory: [],
    u_symptom: [],
    impact: [],
    urgency: [],
    contact_type: [],
    location: []
  });
  const [feedbackState, setFeedbackState] = useState({
    resolution: { rating: "5", notes: "", status: "" },
    triage: { rating: "5", notes: "", status: "" },
    routing: { rating: "5", notes: "", status: "" }
  });
  const [escalationState, setEscalationState] = useState({
    resolution: { status: "" },
    triage: { status: "" },
    routing: { status: "" }
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch(`${API_URL}/api/stats`)
      .then((response) => response.json())
      .then((data) => setStats(data))
      .catch(() => setStats(null));

    fetch(`${API_URL}/api/triage/filters`)
      .then((response) => response.json())
      .then((data) => setTriageFilters(data))
      .catch(() => setTriageFilters({ category: [], ci_category: [], ci_subcategory: [] }));

    fetch(`${API_URL}/api/routing/filters`)
      .then((response) => response.json())
      .then((data) => setRoutingFilters(data))
      .catch(() =>
        setRoutingFilters({
          category: [],
          subcategory: [],
          u_symptom: [],
          impact: [],
          urgency: [],
          contact_type: [],
          location: []
        })
      );
  }, []);

  function resetOtherModes(nextMode) {
    setMode(nextMode);
    setError("");
    if (nextMode !== "resolution") {
      setResolutionResult(null);
    }
    if (nextMode !== "triage") {
      setTriageResult(null);
    }
    if (nextMode !== "routing") {
      setRoutingResult(null);
    }
  }

  function updateFeedbackField(agent, field, value) {
    setFeedbackState((current) => ({
      ...current,
      [agent]: { ...current[agent], [field]: value, status: "" }
    }));
  }

  function updateEscalationStatus(agent, status) {
    setEscalationState((current) => ({
      ...current,
      [agent]: { status }
    }));
  }

  function buildFeedbackPayload(agent) {
    const feedback = feedbackState[agent];
    if (agent === "resolution") {
      return {
        agent,
        rating: Number(feedback.rating),
        feedback: feedback.notes,
        request: { query: resolutionQuery, top_k: 4 },
        response: resolutionResult
      };
    }
    if (agent === "triage") {
      return {
        agent,
        rating: Number(feedback.rating),
        feedback: feedback.notes,
        request: {
          ticket_summary: triageForm.ticketSummary,
          category: triageForm.category,
          ci_category: triageForm.ciCategory,
          ci_subcategory: triageForm.ciSubcategory,
          top_k: 5
        },
        response: triageResult
      };
    }
    return {
      agent,
      rating: Number(feedback.rating),
      feedback: feedback.notes,
      request: {
        description: routingForm.description,
        category: routingForm.category,
        subcategory: routingForm.subcategory,
        u_symptom: routingForm.uSymptom,
        impact: routingForm.impact,
        urgency: routingForm.urgency,
        contact_type: routingForm.contactType,
        location: routingForm.location,
        top_k: 5
      },
      response: routingResult
    };
  }

  async function submitFeedback(agent) {
    setFeedbackState((current) => ({
      ...current,
      [agent]: { ...current[agent], status: "Sending feedback..." }
    }));

    try {
      await submitJson(`${API_URL}/api/feedback`, buildFeedbackPayload(agent));
      setFeedbackState((current) => ({
        ...current,
        [agent]: { ...current[agent], status: "Thanks! Feedback recorded." }
      }));
    } catch (requestError) {
      setFeedbackState((current) => ({
        ...current,
        [agent]: { ...current[agent], status: requestError.message || "Feedback failed." }
      }));
    }
  }

  function getResolutionConfidence() {
    if (!resolutionResult || !resolutionResult.sources?.length) {
      return 0;
    }
    return Math.max(...resolutionResult.sources.map((source) => source.similarity || 0));
  }

  function shouldOfferEscalation(agent) {
    const rating = Number(feedbackState[agent].rating || 0);
    if (rating > 0 && rating <= 2) {
      return true;
    }

    if (agent === "resolution") {
      return getResolutionConfidence() < ESCALATION_L1_THRESHOLD;
    }
    if (agent === "triage" && triageResult) {
      return (triageResult.confidence || 0) < ESCALATION_L2_THRESHOLD;
    }
    if (agent === "routing" && routingResult) {
      return (routingResult.confidence || 0) < ESCALATION_L2_THRESHOLD;
    }
    return false;
  }

  function buildEscalationPayload() {
    return {
      query: resolutionQuery || triageForm.ticketSummary || routingForm.description || "",
      ticket_summary: triageForm.ticketSummary,
      category: triageForm.category || routingForm.category,
      ci_category: triageForm.ciCategory,
      ci_subcategory: triageForm.ciSubcategory,
      subcategory: routingForm.subcategory,
      u_symptom: routingForm.uSymptom,
      impact: routingForm.impact,
      urgency: routingForm.urgency,
      contact_type: routingForm.contactType,
      location: routingForm.location,
      l1_threshold: ESCALATION_L1_THRESHOLD,
      l2_threshold: ESCALATION_L2_THRESHOLD
    };
  }

  async function submitEscalation(agent) {
    updateEscalationStatus(agent, "Escalating...");
    try {
      await submitJson(`${API_URL}/api/escalate`, buildEscalationPayload());
      updateEscalationStatus(agent, "Escalation submitted. Check L2/L3 queue.");
    } catch (requestError) {
      updateEscalationStatus(agent, requestError.message || "Escalation failed.");
    }
  }

  async function submitJson(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });

    if (!response.ok) {
      const details = await response.json().catch(() => ({ detail: "Request failed." }));
      throw new Error(details.detail || "Request failed.");
    }

    return response.json();
  }

  async function handleResolutionSubmit(event) {
    event.preventDefault();
    const clientError = validateFreeText(resolutionQuery, "Resolution query");
    if (clientError) {
      setError(clientError);
      setResolutionResult(null);
      return;
    }

    setLoading(true);
    setError("");
    setTriageResult(null);
    setRoutingResult(null);
    setFeedbackState((current) => ({
      ...current,
      resolution: { ...current.resolution, status: "" }
    }));
    updateEscalationStatus("resolution", "");

    try {
      const data = await submitJson(`${API_URL}/api/resolution`, {
        query: resolutionQuery,
        top_k: 4
      });
      setResolutionResult(data);
    } catch (requestError) {
      setError(requestError.message);
      setResolutionResult(null);
    } finally {
      setLoading(false);
    }
  }

  async function handleTriageSubmit(event) {
    event.preventDefault();
    const clientError = validateFreeText(triageForm.ticketSummary, "Ticket summary");
    if (clientError) {
      setError(clientError);
      setTriageResult(null);
      return;
    }

    setLoading(true);
    setError("");
    setResolutionResult(null);
    setRoutingResult(null);
    setFeedbackState((current) => ({
      ...current,
      triage: { ...current.triage, status: "" }
    }));
    updateEscalationStatus("triage", "");

    try {
      const data = await submitJson(`${API_URL}/api/triage`, {
        ticket_summary: triageForm.ticketSummary,
        category: triageForm.category,
        ci_category: triageForm.ciCategory,
        ci_subcategory: triageForm.ciSubcategory,
        top_k: 5
      });
      setTriageResult(data);
    } catch (requestError) {
      setError(requestError.message);
      setTriageResult(null);
    } finally {
      setLoading(false);
    }
  }

  async function handleRoutingSubmit(event) {
    event.preventDefault();
    const clientError = routingForm.description
      ? validateFreeText(routingForm.description, "Routing description")
      : "";
    if (clientError) {
      setError(clientError);
      setRoutingResult(null);
      return;
    }

    setLoading(true);
    setError("");
    setResolutionResult(null);
    setTriageResult(null);
    setFeedbackState((current) => ({
      ...current,
      routing: { ...current.routing, status: "" }
    }));
    updateEscalationStatus("routing", "");

    try {
      const data = await submitJson(`${API_URL}/api/routing`, {
        description: routingForm.description,
        category: routingForm.category,
        subcategory: routingForm.subcategory,
        u_symptom: routingForm.uSymptom,
        impact: routingForm.impact,
        urgency: routingForm.urgency,
        contact_type: routingForm.contactType,
        location: routingForm.location,
        top_k: 5
      });
      setRoutingResult(data);
    } catch (requestError) {
      setError(requestError.message);
      setRoutingResult(null);
    } finally {
      setLoading(false);
    }
  }

  function updateTriageField(field, value) {
    setTriageForm((current) => ({ ...current, [field]: value }));
  }

  function updateRoutingField(field, value) {
    setRoutingForm((current) => ({ ...current, [field]: value }));
  }

  return (
    <main className="page-shell">
      <section className="hero">
        <p className="eyebrow">IT Support Copilot</p>
        <h1>Resolve incidents, classify priority, and route tickets from historical support data.</h1>
        <p className="hero-copy">
          Use the curated response dataset for resolution suggestions, the ITSM dataset for triage,
          and the incident event log for assignment-group routing.
        </p>
        {stats ? (
          <div className="stats-card">
            <span>{stats.indexed_incidents} resolution incidents</span>
            <span>{stats.triage_records} triage tickets</span>
            <span>{stats.routing_records} routing tickets</span>
          </div>
        ) : null}
      </section>

      <div className="mode-switch">
        <button
          type="button"
          className={mode === "resolution" ? "mode-button active" : "mode-button"}
          onClick={() => resetOtherModes("resolution")}
        >
          Resolution Bot
        </button>
        <button
          type="button"
          className={mode === "triage" ? "mode-button active" : "mode-button"}
          onClick={() => resetOtherModes("triage")}
        >
          Triage Agent
        </button>
        <button
          type="button"
          className={mode === "routing" ? "mode-button active" : "mode-button"}
          onClick={() => resetOtherModes("routing")}
        >
          Routing Agent
        </button>
      </div>

      <section className="workspace">
        {mode === "resolution" ? (
          <form className="query-panel" onSubmit={handleResolutionSubmit}>
            <label htmlFor="resolution-query" className="label">
              Describe the incident
            </label>
            <textarea
              id="resolution-query"
              rows="7"
              value={resolutionQuery}
              onChange={(event) => setResolutionQuery(event.target.value)}
              placeholder="Describe the symptoms, impact, and what users are seeing."
            />
            <p className="guardrail-note">
              Guardrails reject empty, vague, repetitive, or prompt-instruction text.
            </p>
            <div className="sample-list">
              {resolutionSamples.map((question) => (
                <button
                  key={question}
                  type="button"
                  className="sample-chip"
                  onClick={() => setResolutionQuery(question)}
                >
                  {question}
                </button>
              ))}
            </div>
            <button type="submit" className="submit-button" disabled={loading}>
              {loading ? "Suggesting resolution..." : "Get resolution suggestion"}
            </button>
          </form>
        ) : mode === "triage" ? (
          <form className="query-panel" onSubmit={handleTriageSubmit}>
            <label htmlFor="triage-summary" className="label">
              Ticket summary
            </label>
            <textarea
              id="triage-summary"
              rows="5"
              value={triageForm.ticketSummary}
              onChange={(event) => updateTriageField("ticketSummary", event.target.value)}
              placeholder="Describe the issue clearly."
            />
            <p className="guardrail-note">
              Use a real issue description, not instructions to the assistant.
            </p>

            <div className="triage-grid">
              <label className="field-group">
                <span>Category</span>
                <select
                  value={triageForm.category}
                  onChange={(event) => updateTriageField("category", event.target.value)}
                >
                  <option value="">Select category</option>
                  {triageFilters.category.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field-group">
                <span>CI category</span>
                <select
                  value={triageForm.ciCategory}
                  onChange={(event) => updateTriageField("ciCategory", event.target.value)}
                >
                  <option value="">Select CI category</option>
                  {triageFilters.ci_category.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field-group">
                <span>CI subcategory</span>
                <select
                  value={triageForm.ciSubcategory}
                  onChange={(event) => updateTriageField("ciSubcategory", event.target.value)}
                >
                  <option value="">Select CI subcategory</option>
                  {triageFilters.ci_subcategory.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <div className="sample-list">
              {triageSamples.map((sample, index) => (
                <button
                  key={index}
                  type="button"
                  className="sample-chip"
                  onClick={() => setTriageForm(sample)}
                >
                  Load triage sample {index + 1}
                </button>
              ))}
            </div>

            <button type="submit" className="submit-button" disabled={loading}>
              {loading ? "Classifying priority..." : "Classify priority"}
            </button>
          </form>
        ) : (
          <form className="query-panel" onSubmit={handleRoutingSubmit}>
            <label htmlFor="routing-summary" className="label">
              Description
            </label>
            <textarea
              id="routing-summary"
              rows="5"
              value={routingForm.description}
              onChange={(event) => updateRoutingField("description", event.target.value)}
              placeholder="Optional extra description to improve routing."
            />
            <p className="guardrail-note">
              Routing uses the structured incident fields below. Description is optional extra context.
            </p>

            <div className="triage-grid">
              <label className="field-group">
                <span>Category</span>
                <select
                  value={routingForm.category}
                  onChange={(event) => updateRoutingField("category", event.target.value)}
                >
                  <option value="">Any category</option>
                  {routingFilters.category.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field-group">
                <span>Subcategory</span>
                <select
                  value={routingForm.subcategory}
                  onChange={(event) => updateRoutingField("subcategory", event.target.value)}
                >
                  <option value="">Any subcategory</option>
                  {routingFilters.subcategory.slice(0, 200).map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field-group">
                <span>u_symptom</span>
                <select
                  value={routingForm.uSymptom}
                  onChange={(event) => updateRoutingField("uSymptom", event.target.value)}
                >
                  <option value="">Select symptom</option>
                  {routingFilters.u_symptom.slice(0, 300).map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field-group">
                <span>Impact</span>
                <select
                  value={routingForm.impact}
                  onChange={(event) => updateRoutingField("impact", event.target.value)}
                >
                  <option value="">Select impact</option>
                  {routingFilters.impact.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field-group">
                <span>Urgency</span>
                <select
                  value={routingForm.urgency}
                  onChange={(event) => updateRoutingField("urgency", event.target.value)}
                >
                  <option value="">Select urgency</option>
                  {routingFilters.urgency.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field-group">
                <span>Contact type</span>
                <select
                  value={routingForm.contactType}
                  onChange={(event) => updateRoutingField("contactType", event.target.value)}
                >
                  <option value="">Optional</option>
                  {routingFilters.contact_type.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field-group">
                <span>Location</span>
                <select
                  value={routingForm.location}
                  onChange={(event) => updateRoutingField("location", event.target.value)}
                >
                  <option value="">Optional</option>
                  {routingFilters.location.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <div className="sample-list">
              {routingSamples.map((sample, index) => (
                <button
                  key={index}
                  type="button"
                  className="sample-chip"
                  onClick={() => setRoutingForm(sample)}
                >
                  Load routing sample {index + 1}
                </button>
              ))}
            </div>

            <button type="submit" className="submit-button" disabled={loading}>
              {loading ? "Suggesting assignment group..." : "Suggest ticket routing"}
            </button>
          </form>
        )}

        <section className="results-panel">
          <div className="result-card">
            <p className="section-title">
              {mode === "resolution"
                ? "Resolution suggestion"
                : mode === "triage"
                  ? "Triage recommendation"
                  : "Routing recommendation"}
            </p>
            {error ? <p className="error-text">{error}</p> : null}

            {mode === "resolution" ? (
              !resolutionResult && !error ? (
                <p className="placeholder-text">
                  Ask an incident question to retrieve similar historical resolutions.
                </p>
              ) : resolutionResult ? (
                <>
                  <pre className="answer-text">{resolutionResult.answer}</pre>
                  <div className="feedback-panel">
                    <div className="feedback-row">
                      <label className="field-group">
                        <span>Rating</span>
                        <select
                          value={feedbackState.resolution.rating}
                          onChange={(event) => updateFeedbackField("resolution", "rating", event.target.value)}
                        >
                          {[5, 4, 3, 2, 1].map((value) => (
                            <option key={value} value={value}>
                              {value}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="field-group">
                        <span>Feedback</span>
                        <input
                          type="text"
                          value={feedbackState.resolution.notes}
                          onChange={(event) => updateFeedbackField("resolution", "notes", event.target.value)}
                          placeholder="What worked or what should improve?"
                        />
                      </label>
                    </div>
                    <div className="feedback-actions">
                      <button
                        type="button"
                        className="submit-button"
                        onClick={() => submitFeedback("resolution")}
                      >
                        Send feedback
                      </button>
                      {shouldOfferEscalation("resolution") ? (
                        <button
                          type="button"
                          className="secondary-button"
                          onClick={() => submitEscalation("resolution")}
                        >
                          Escalate to L2/L3
                        </button>
                      ) : null}
                      {feedbackState.resolution.status ? (
                        <span className="feedback-status">{feedbackState.resolution.status}</span>
                      ) : null}
                      {escalationState.resolution.status ? (
                        <span className="feedback-status">{escalationState.resolution.status}</span>
                      ) : null}
                    </div>
                  </div>
                </>
              ) : null
            ) : mode === "triage" ? (
              !triageResult && !error ? (
                <p className="placeholder-text">
                  Enter ticket details to predict priority, impact, and urgency.
                </p>
              ) : triageResult ? (
                <div className="triage-summary">
                <div className="triage-badges">
                  <span className="triage-badge">Priority {triageResult.priority}</span>
                  <span className="triage-badge">Impact {triageResult.impact}</span>
                  <span className="triage-badge">Urgency {triageResult.urgency}</span>
                    <span className="triage-badge">
                      Confidence {Math.round(triageResult.confidence * 100)}%
                    </span>
                    {triageResult.predicted_resolution_time_minutes !== null &&
                    triageResult.predicted_resolution_time_minutes !== undefined ? (
                      <span className="triage-badge">
                        Predicted resolution{" "}
                        {Math.round(triageResult.predicted_resolution_time_minutes)} min
                      </span>
                    ) : null}
                </div>
                {triageResult.resolution_time_mae_minutes !== null &&
                triageResult.resolution_time_mae_minutes !== undefined ? (
                  <p className="guardrail-note">
                    Resolution time model MAE {Math.round(triageResult.resolution_time_mae_minutes)} min, RMSE{" "}
                    {Math.round(triageResult.resolution_time_rmse_minutes)} min (train{" "}
                    {triageResult.resolution_time_train_samples}, test{" "}
                    {triageResult.resolution_time_test_samples}).
                  </p>
                ) : null}
                <pre className="answer-text">{triageResult.rationale}</pre>
                <div className="feedback-panel">
                  <div className="feedback-row">
                    <label className="field-group">
                      <span>Rating</span>
                      <select
                        value={feedbackState.triage.rating}
                        onChange={(event) => updateFeedbackField("triage", "rating", event.target.value)}
                      >
                        {[5, 4, 3, 2, 1].map((value) => (
                          <option key={value} value={value}>
                            {value}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="field-group">
                      <span>Feedback</span>
                      <input
                        type="text"
                        value={feedbackState.triage.notes}
                        onChange={(event) => updateFeedbackField("triage", "notes", event.target.value)}
                        placeholder="Was the priority/impact/urgency right?"
                      />
                    </label>
                  </div>
                  <div className="feedback-actions">
                    <button
                      type="button"
                      className="submit-button"
                      onClick={() => submitFeedback("triage")}
                    >
                      Send feedback
                    </button>
                    {shouldOfferEscalation("triage") ? (
                      <button
                        type="button"
                        className="secondary-button"
                        onClick={() => submitEscalation("triage")}
                      >
                        Escalate to L3
                      </button>
                    ) : null}
                    {feedbackState.triage.status ? (
                      <span className="feedback-status">{feedbackState.triage.status}</span>
                    ) : null}
                    {escalationState.triage.status ? (
                      <span className="feedback-status">{escalationState.triage.status}</span>
                    ) : null}
                  </div>
                </div>
              </div>
            ) : null
            ) : !routingResult && !error ? (
              <p className="placeholder-text">
                Enter incident details to recommend the historical assignment group.
              </p>
            ) : routingResult ? (
              <div className="triage-summary">
                <div className="triage-badges">
                  <span className="triage-badge">{routingResult.assignment_group}</span>
                  <span className="triage-badge">
                    Confidence {Math.round(routingResult.confidence * 100)}%
                  </span>
                </div>
                <pre className="answer-text">{routingResult.rationale}</pre>
                <div className="feedback-panel">
                  <div className="feedback-row">
                    <label className="field-group">
                      <span>Rating</span>
                      <select
                        value={feedbackState.routing.rating}
                        onChange={(event) => updateFeedbackField("routing", "rating", event.target.value)}
                      >
                        {[5, 4, 3, 2, 1].map((value) => (
                          <option key={value} value={value}>
                            {value}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="field-group">
                      <span>Feedback</span>
                      <input
                        type="text"
                        value={feedbackState.routing.notes}
                        onChange={(event) => updateFeedbackField("routing", "notes", event.target.value)}
                        placeholder="Was the routing group appropriate?"
                      />
                    </label>
                  </div>
                  <div className="feedback-actions">
                    <button
                      type="button"
                      className="submit-button"
                      onClick={() => submitFeedback("routing")}
                    >
                      Send feedback
                    </button>
                    {shouldOfferEscalation("routing") ? (
                      <button
                        type="button"
                        className="secondary-button"
                        onClick={() => submitEscalation("routing")}
                      >
                        Escalate to L3
                      </button>
                    ) : null}
                    {feedbackState.routing.status ? (
                      <span className="feedback-status">{feedbackState.routing.status}</span>
                    ) : null}
                    {escalationState.routing.status ? (
                      <span className="feedback-status">{escalationState.routing.status}</span>
                    ) : null}
                  </div>
                </div>
              </div>
            ) : null}
          </div>

          <div className="result-card">
            <p className="section-title">
              {mode === "resolution"
                ? "Supporting incidents"
                : mode === "triage"
                  ? "Supporting historical tickets"
                  : "Supporting routing tickets"}
            </p>

            {mode === "resolution" ? (
              !resolutionResult ? (
                <p className="placeholder-text">
                  Matching incidents will appear here with their descriptions and solutions.
                </p>
              ) : (
                <div className="sources-grid">
                  {resolutionResult.sources.map((source) => (
                    <article key={source.incident_id} className="source-card">
                      <div className="source-meta">
                        <span>{source.incident_id}</span>
                        <span>{Math.round(source.similarity * 100)}% match</span>
                      </div>
                      <p>
                        <strong>Semantic score:</strong> {Math.round(source.semantic_score * 100)}%
                      </p>
                      <p>
                        <strong>Keyword score:</strong> {Math.round(source.keyword_score * 100)}%
                      </p>
                      <h2>{source.incident_details}</h2>
                      <p>
                        <strong>Category:</strong> {source.category}
                      </p>
                      <p>
                        <strong>Media asset:</strong> {source.media_asset}
                      </p>
                      <p>
                        <strong>Description:</strong> {source.description}
                      </p>
                      <p>
                        <strong>Resolution:</strong> {source.solution}
                      </p>
                    </article>
                  ))}
                </div>
              )
            ) : mode === "triage" ? (
              !triageResult ? (
                <p className="placeholder-text">
                  Similar ITSM tickets will appear here with their priority, impact, and urgency values.
                </p>
              ) : (
                <div className="sources-grid">
                  {triageResult.sources.map((source) => (
                    <article key={source.incident_id} className="source-card">
                      <div className="source-meta">
                        <span>{source.incident_id}</span>
                        <span>{Math.round(source.similarity * 100)}% match</span>
                      </div>
                      <div className="triage-badges compact">
                        <span className="triage-badge">P{source.priority}</span>
                        <span className="triage-badge">I{source.impact}</span>
                        <span className="triage-badge">U{source.urgency}</span>
                      </div>
                      <p>
                        <strong>Ticket category:</strong> {source.category}
                      </p>
                      <p>
                        <strong>CI:</strong> {source.ci_cat || "Unknown"} / {source.ci_subcat || "Unknown"}
                      </p>
                      <p>
                        <strong>Status:</strong> {source.status}
                      </p>
                      <p>
                        <strong>Keyword score:</strong> {Math.round(source.keyword_score * 100)}%
                      </p>
                      <p>
                        <strong>Field score:</strong> {Math.round(source.field_score * 100)}%
                      </p>
                    </article>
                  ))}
                </div>
              )
            ) : !routingResult ? (
              <p className="placeholder-text">
                Similar event-log tickets will appear here with their historical assignment groups.
              </p>
            ) : (
              <div className="sources-grid">
                {routingResult.sources.map((source) => (
                  <article key={source.incident_id} className="source-card">
                    <div className="source-meta">
                      <span>{source.incident_id}</span>
                      <span>{Math.round(source.similarity * 100)}% match</span>
                    </div>
                    <div className="triage-badges compact">
                      <span className="triage-badge">{source.assignment_group}</span>
                      <span className="triage-badge">{source.impact || "No impact"}</span>
                      <span className="triage-badge">{source.urgency || "No urgency"}</span>
                    </div>
                    <p>
                      <strong>Category:</strong> {source.category || "Unknown"}
                    </p>
                    <p>
                      <strong>Subcategory:</strong> {source.subcategory || "Unknown"}
                    </p>
                    <p>
                      <strong>Symptom:</strong> {source.symptom || "Unknown"}
                    </p>
                    <p>
                      <strong>Contact type:</strong> {source.contact_type || "Unknown"}
                    </p>
                    <p>
                      <strong>Location:</strong> {source.location || "Unknown"}
                    </p>
                    <p>
                      <strong>Keyword score:</strong> {Math.round(source.keyword_score * 100)}%
                    </p>
                    <p>
                      <strong>Field score:</strong> {Math.round(source.field_score * 100)}%
                    </p>
                  </article>
                ))}
              </div>
            )}
          </div>
        </section>
      </section>
    </main>
  );
}

export default App;
