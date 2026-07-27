/**
 * Todo 状态管理（多 session 版本）
 *
 * Todo 列表按 session 隔离存储在 runtimes 中。
 */

import { create } from 'zustand';
import {
  TodoItem,
  TodoStatus,
  isTodoStatusRegression,
  normalizeTodoStatus,
} from '../types';

interface TodoRuntime {
  todos: TodoItem[];
}

function createEmptyRuntime(): TodoRuntime {
  return { todos: [] };
}

function normalizeTodoItem(todo: TodoItem): TodoItem {
  return {
    ...todo,
    status: normalizeTodoStatus(todo.status),
  };
}

interface TodoState {
  runtimes: Record<string, TodoRuntime>;

  ensureRuntime: (sessionId: string) => TodoRuntime;
  getRuntime: (sessionId: string | null) => TodoRuntime | undefined;
  removeRuntime: (sessionId: string) => void;

  setTodos: (sessionId: string, todos: TodoItem[]) => void;
  addTodo: (sessionId: string, todo: TodoItem) => void;
  updateTodo: (sessionId: string, id: string, updates: Partial<TodoItem>) => void;
  updateTodoStatus: (sessionId: string, id: string, status: TodoStatus) => void;
  removeTodo: (sessionId: string, id: string) => void;
  clearTodos: (sessionId: string) => void;
}

export const useTodoStore = create<TodoState>((set, get) => ({
  runtimes: {},

  ensureRuntime: (sessionId) => {
    const existing = get().runtimes[sessionId];
    if (existing) return existing;
    const runtime = createEmptyRuntime();
    set((state) => ({
      runtimes: { ...state.runtimes, [sessionId]: runtime },
    }));
    return runtime;
  },

  getRuntime: (sessionId) => {
    if (!sessionId) return undefined;
    return get().runtimes[sessionId];
  },

  removeRuntime: (sessionId) => {
    set((state) => {
      const next = { ...state.runtimes };
      delete next[sessionId];
      return { runtimes: next };
    });
  },

  setTodos: (sessionId, todos) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      const prevById = new Map<string, TodoItem>();
      runtime.todos.forEach((todo) => {
        if (!prevById.has(todo.id)) prevById.set(todo.id, todo);
      });

      const mergedTodos = todos.map((raw) => {
        const todo = normalizeTodoItem(raw);
        const prev = prevById.get(todo.id);
        let status = todo.status;
        // session_result may flip UI to completed before model updates todos;
        // do not let a stale snapshot regress terminal status.
        if (prev && isTodoStatusRegression(prev.status, status)) {
          status = prev.status;
        }
        const wasInProgress = prev?.status === 'in_progress';
        if (status === 'in_progress' && !wasInProgress) {
          return { ...todo, status, updatedAt: new Date().toISOString() };
        }
        if (status === 'in_progress' && prev?.updatedAt) {
          return { ...todo, status, updatedAt: prev.updatedAt };
        }
        return { ...todo, status };
      });

      // Keep local session_result / spawn synthesized rows not present in snapshot.
      const incomingIds = new Set(mergedTodos.map((todo) => todo.id));
      const preservedLocal = runtime.todos.filter(
        (todo) =>
          todo.id.startsWith('session-task-') && !incomingIds.has(todo.id)
      );

      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: {
            ...runtime,
            todos: [...mergedTodos, ...preservedLocal],
          },
        },
      };
    });
  },

  addTodo: (sessionId, todo) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: {
            ...runtime,
            todos: [...runtime.todos, normalizeTodoItem(todo)],
          },
        },
      };
    });
  },

  updateTodo: (sessionId, id, updates) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      const normalizedUpdates =
        updates.status !== undefined
          ? { ...updates, status: normalizeTodoStatus(updates.status) }
          : updates;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: {
            ...runtime,
            todos: runtime.todos.map((todo) =>
              todo.id === id
                ? {
                    ...todo,
                    ...normalizedUpdates,
                    updatedAt: new Date().toISOString(),
                  }
                : todo
            ),
          },
        },
      };
    });
  },

  updateTodoStatus: (sessionId, id, status) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      const normalized = normalizeTodoStatus(status);
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: {
            ...runtime,
            todos: runtime.todos.map((todo) =>
              todo.id === id
                ? {
                    ...todo,
                    status: normalized,
                    updatedAt: new Date().toISOString(),
                  }
                : todo
            ),
          },
        },
      };
    });
  },

  removeTodo: (sessionId, id) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: {
            ...runtime,
            todos: runtime.todos.filter((todo) => todo.id !== id),
          },
        },
      };
    });
  },

  clearTodos: (sessionId) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, todos: [] },
        },
      };
    });
  },
}));
