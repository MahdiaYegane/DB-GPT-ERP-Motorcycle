import { ChatContext, ChatContextProvider } from '@/app/chat-context';
import SideBar from '@/components/layout/side-bar';
import { NewTaskProvider } from '@/modules/new-task';
import { STORAGE_USERINFO_KEY, STORAGE_USERINFO_VALID_TIME_KEY } from '@/utils/constants/index';
import { App, ConfigProvider, MappingAlgorithm, theme } from 'antd';
import enUS from 'antd/locale/en_US';
import classNames from 'classnames';
import type { AppProps } from 'next/app';
import Head from 'next/head';
import { useRouter } from 'next/router';
import React, { useContext, useEffect, useState } from 'react';
import '../app/i18n';
import '../nprogress.css';
import '../styles/globals.css';
import '../styles/kavir-theme.css';

// import TopProgressBar from '@/components/layout/top-progress-bar';

const antdDarkTheme: MappingAlgorithm = (seedToken, mapToken) => {
  return {
    ...theme.darkAlgorithm(seedToken, mapToken),
    colorBgBase: '#232734',
    colorBorder: '#828282',
    colorBgContainer: '#232734',
  };
};

function CssWrapper({ children }: { children: React.ReactElement }) {
  const { mode } = useContext(ChatContext);

  useEffect(() => {
    if (mode) {
      document.body?.classList?.add(mode);
      if (mode === 'light') {
        document.body?.classList?.remove('dark');
      } else {
        document.body?.classList?.remove('light');
      }
    }
  }, [mode]);

  useEffect(() => {
    document.documentElement.lang = 'en';
    document.documentElement.dir = 'rtl';
    document.body.dir = 'rtl';
  }, []);

  return (
    <div>
      {/* <TopProgressBar /> */}
      {children}
    </div>
  );
}

function LayoutWrapper({ children }: { children: React.ReactNode }) {
  const { isMenuExpand, mode } = useContext(ChatContext);
  const [isLogin, setIsLogin] = useState(false);

  const router = useRouter();

  // ç™»ه½•و£€وµ‹
  const handleAuth = async () => {
    setIsLogin(false);
    // ه¦‚و‍œه·²وœ‰ç™»ه½•ن؟،وپ¯ï¼Œç›´وژ¥ه±•ç¤؛é¦–é،µ
    // if (localStorage.getItem(STORAGE_USERINFO_KEY)) {
    //   setIsLogin(true);
    //   return;
    // }

    // MOCK User info
    const user = {
      user_channel: `dbgpt`,
      user_no: `001`,
      nick_name: `dbgpt`,
    };
    if (user) {
      localStorage.setItem(STORAGE_USERINFO_KEY, JSON.stringify(user));
      localStorage.setItem(STORAGE_USERINFO_VALID_TIME_KEY, Date.now().toString());
      setIsLogin(true);
    }
  };

  useEffect(() => {
    handleAuth();
  }, []);

  if (!isLogin && !router.pathname.startsWith('/share')) {
    return null;
  }

  const renderContent = () => {
    // Hide sidebar for mobile, share pages, and task replay mode (from_task)
    const hideSidebar =
      router.pathname.includes('mobile') || router.pathname.startsWith('/share') || !!router.query.from_task;

    if (router.pathname.includes('mobile') || router.pathname.startsWith('/share')) {
      return <>{children}</>;
    }
    return (
      <div className='flex w-screen h-screen overflow-hidden'>
        <Head>
          <meta name='viewport' content='initial-scale=1.0, width=device-width, maximum-scale=1' />
        </Head>
        {router.pathname !== '/construct/app/extra' && !hideSidebar && (
          <div className={classNames('transition-[width]', isMenuExpand ? 'w-60' : 'w-20', 'hidden', 'md:block')}>
            <SideBar />
          </div>
        )}
        <div className='flex flex-col flex-1 relative overflow-hidden'>{children}</div>
      </div>
    );
  };

  return (
    <ConfigProvider
      locale={enUS}
      direction='rtl'
      theme={{
        token: {
          colorPrimary: '#E30613',
          borderRadius: 4,
          fontFamily: 'Arad, sans-serif',
        },
        algorithm: mode === 'dark' ? antdDarkTheme : undefined,
      }}
    >
      <App>{renderContent()}</App>
    </ConfigProvider>
  );
}

function MyApp({ Component, pageProps }: AppProps) {
  return (
    <ChatContextProvider>
      <NewTaskProvider>
        <CssWrapper>
          <LayoutWrapper>
            <Component {...pageProps} />
          </LayoutWrapper>
        </CssWrapper>
      </NewTaskProvider>
    </ChatContextProvider>
  );
}

export default MyApp;
