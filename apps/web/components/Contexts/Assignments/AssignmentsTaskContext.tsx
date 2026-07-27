'use client'
import React, { createContext, useContext, useEffect, useReducer, useRef } from 'react'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { getAssignmentTask } from '@services/courses/assignments'
import { useAssignments } from './AssignmentContext';
import { useQueryClient } from '@tanstack/react-query';
import { queryKeys } from '@/lib/query/keys';

interface State {
    selectedAssignmentTaskUUID: string | null;
    assignmentTask: Record<string, any>;
    isLoadingAssignmentTask: boolean;
    assignmentTaskError: string | null;
    reloadTrigger: number;
}

interface Action {
    type: string;
    payload?: any;
}

const initialState: State = {
    selectedAssignmentTaskUUID: null,
    assignmentTask: {},
    isLoadingAssignmentTask: false,
    assignmentTaskError: null,
    reloadTrigger: 0,
};

export const AssignmentsTaskContext = createContext<State | undefined>(undefined);
export const AssignmentsTaskDispatchContext = createContext<React.Dispatch<Action> | undefined>(undefined);

export function AssignmentsTaskProvider({ children }: { children: React.ReactNode }) {
    const session = useLHSession() as any;
    const access_token = session?.data?.tokens?.access_token;
    const assignment = useAssignments() as any
    const queryClient = useQueryClient();
    const latestRequestedTaskUUIDRef = useRef<string | null>(null);

    const [state, dispatch] = useReducer(assignmentstaskReducer, initialState);
    const assignmentUuid = assignment.assignment_object?.assignment_uuid;

    async function fetchAssignmentTask(assignmentTaskUUID: string) {
        latestRequestedTaskUUIDRef.current = assignmentTaskUUID;
        dispatch({ type: 'startAssignmentTaskFetch', payload: assignmentTaskUUID });

        if (!access_token) {
            dispatch({ type: 'setAssignmentTaskFetchError', payload: '請先登入後再讀取題目。' });
            return;
        }

        try {
            const res = await getAssignmentTask(assignmentTaskUUID, access_token);
            if (latestRequestedTaskUUIDRef.current !== assignmentTaskUUID) return;

            if (res.success) {
                dispatch({ type: 'setAssignmentTask', payload: res.data ?? {} });
                return;
            }

            dispatch({
                type: 'setAssignmentTaskFetchError',
                payload: getAssignmentTaskErrorMessage(res),
            });
        } catch (error) {
            if (latestRequestedTaskUUIDRef.current !== assignmentTaskUUID) return;
            dispatch({
                type: 'setAssignmentTaskFetchError',
                payload: getAssignmentTaskErrorMessage(error),
            });
        }
    }

    useEffect(() => {
        if (!state.selectedAssignmentTaskUUID) {
            latestRequestedTaskUUIDRef.current = null;
            return;
        }

        if (state.selectedAssignmentTaskUUID) {
            fetchAssignmentTask(state.selectedAssignmentTaskUUID);
            if (assignmentUuid) {
                queryClient.invalidateQueries({ queryKey: queryKeys.assignments.tasks(assignmentUuid) });
            }
        }
    }, [state.selectedAssignmentTaskUUID, state.reloadTrigger, assignmentUuid, access_token, queryClient]);

    return (
        <AssignmentsTaskContext.Provider value={state}>
            <AssignmentsTaskDispatchContext.Provider value={dispatch}>
                {children}
            </AssignmentsTaskDispatchContext.Provider>
        </AssignmentsTaskContext.Provider>
    );
}

export function useAssignmentsTask() {
    const context = useContext(AssignmentsTaskContext);
    if (context === undefined) {
        throw new Error('useAssignmentsTask must be used within an AssignmentsTaskProvider');
    }
    return context;
}

export function useAssignmentsTaskDispatch() {
    const context = useContext(AssignmentsTaskDispatchContext);
    if (context === undefined) {
        throw new Error('useAssignmentsTaskDispatch must be used within an AssignmentsTaskProvider');
    }
    return context;
}

function getAssignmentTaskErrorMessage(error: any): string {
    const detail = error?.data?.detail ?? error?.detail ?? error?.message
    if (typeof detail === 'string' && detail.trim()) return detail
    return '讀取題目失敗，請重新整理頁面後再試。'
}

function assignmentstaskReducer(state: State, action: Action): State {
    switch (action.type) {
        case 'setSelectedAssignmentTaskUUID':
            if (action.payload === state.selectedAssignmentTaskUUID) return state;
            return {
                ...state,
                selectedAssignmentTaskUUID: action.payload,
                assignmentTask: {},
                isLoadingAssignmentTask: Boolean(action.payload),
                assignmentTaskError: null,
            };
        case 'startAssignmentTaskFetch':
            return {
                ...state,
                assignmentTask: {},
                isLoadingAssignmentTask: true,
                assignmentTaskError: null,
            };
        case 'setAssignmentTask':
            return {
                ...state,
                assignmentTask: action.payload,
                isLoadingAssignmentTask: false,
                assignmentTaskError: null,
            };
        case 'setAssignmentTaskFetchError':
            return {
                ...state,
                assignmentTask: {},
                isLoadingAssignmentTask: false,
                assignmentTaskError: action.payload,
            };
        case 'reload':
            return {
                ...state,
                reloadTrigger: state.reloadTrigger + 1,
                isLoadingAssignmentTask: Boolean(state.selectedAssignmentTaskUUID),
                assignmentTaskError: null,
            };
        case 'SET_MULTIPLE_STATES':
            return {
                ...state,
                ...action.payload,
            };
        default:
            return state;
    }
}
