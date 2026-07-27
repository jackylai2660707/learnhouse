'use client';
import React, { useEffect, useCallback, useMemo } from 'react';
import { useLHSession } from '@components/Contexts/LHSessionContext';
import useAdminStatus from '@components/Hooks/useAdminStatus';
import { usePathname, useRouter } from 'next/navigation';
import PageLoading from '@components/Objects/Loaders/PageLoading';
import { getUriWithOrg } from '@services/config/config';
import { useOrgMembership } from '@components/Contexts/OrgContext';

type AuthorizationProps = {
  children: React.ReactNode;
  authorizationMode: 'component' | 'page';
};

const ADMIN_PATHS = [
  '/dash/org/*',
  '/dash/org',
  '/dash/users/*',
  '/dash/users',
  '/dash/courses/*',
  '/dash/courses',
  '/dash/org/settings/general',
];

const AdminAuthorization: React.FC<AuthorizationProps> = ({ children, authorizationMode }) => {
  const session = useLHSession() as any;
  const { org, orgslug } = useOrgMembership();
  const pathname = usePathname();
  const router = useRouter();
  const { isAdmin, loading } = useAdminStatus() as any

  const isUserAuthenticated = useMemo(() => session.status === 'authenticated', [session.status]);

  const checkPathname = useCallback((pattern: string, pathname: string) => {
    // Ensure the inputs are strings
    if (typeof pattern !== 'string' || typeof pathname !== 'string') {
      return false;
    }

    const regexPattern = pattern
      .split('*')
      .map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
      .join('.*');

    return new RegExp(`^${regexPattern}$`).test(pathname);
  }, []);


  const isAdminPath = useMemo(() => ADMIN_PATHS.some(path => checkPathname(path, pathname)), [pathname, checkPathname]);

  const isAuthorized = isUserAuthenticated && (
    authorizationMode === 'component'
      ? isAdmin
      : !isAdminPath || isAdmin
  );

  useEffect(() => {
    if (loading || session.status === 'loading') {
      return;
    }

    if (!isUserAuthenticated) {
      const loginOrgSlug = org?.slug || orgslug;
      router.replace(loginOrgSlug ? getUriWithOrg(loginOrgSlug, '/login') : '/login');
      return;
    }

    if (authorizationMode === 'page' && isAdminPath && !isAdmin) {
      router.replace('/dash');
    }
  }, [loading, session.status, isUserAuthenticated, isAdmin, isAdminPath, authorizationMode, router, org?.slug, orgslug]);

  if (loading || session.status === 'loading' || !isUserAuthenticated) {
    return (
      <div className="flex justify-center items-center h-screen">
        <PageLoading />
      </div>
    );
  }

  if (authorizationMode === 'page' && !isAuthorized) {
    return (
      <div className="flex justify-center items-center h-screen">
        <h1 className="text-2xl">你沒有權限存取此頁面</h1>
      </div>
    );
  }

  return <>{isAuthorized && children}</>;
};

export default AdminAuthorization;
