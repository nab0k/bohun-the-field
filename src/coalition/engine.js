// BOHUN / THE COALITION: isolated, deterministic first playable loop.
// Fictional game parameters. No claims about real-world outcomes.
export const PROJECTS = Object.freeze({
  research: { label: 'Fund joint research', cost: 25, weeks: 2, trust: 3, knowledge: 12 },
  dialogue: { label: 'Open diplomatic dialogue', cost: 10, weeks: 1, trust: 8, knowledge: 0 },
  pilot: { label: 'Support industrial pilot', cost: 40, weeks: 3, trust: 2, knowledge: 6 },
});
export const initialCampaign = () => ({
  version: 1, week: 1, budget: 100, trust: 40, knowledge: 0,
  projects: [], history: [], nextId: 1,
});
export function startProject(state, type, organisationId) {
  const project = PROJECTS[type];
  if (!project) return { ok: false, reason: 'unknown-project', state };
  if (typeof organisationId !== 'string' || !organisationId.trim()) return { ok: false, reason: 'missing-organisation', state };
  if (state.budget < project.cost) return { ok: false, reason: 'insufficient-budget', state };
  const id = state.nextId;
  return { ok: true, state: {
    ...state, budget: state.budget - project.cost, nextId: id + 1,
    projects: [...state.projects, { id, type, organisationId, dueWeek: state.week + project.weeks }],
    history: [...state.history, { week: state.week, event: 'started', id, type, organisationId }],
  } };
}
export function advanceWeek(state) {
  const week = state.week + 1;
  const completed = state.projects.filter(p => p.dueWeek <= week);
  return {
    ...state, week,
    trust: Math.min(100, state.trust + completed.reduce((n,p) => n + PROJECTS[p.type].trust, 0)),
    knowledge: state.knowledge + completed.reduce((n,p) => n + PROJECTS[p.type].knowledge, 0),
    projects: state.projects.filter(p => p.dueWeek > week),
    history: [...state.history, ...completed.map(p => ({ week, event: 'completed', id: p.id, type: p.type, organisationId: p.organisationId }))],
  };
}
