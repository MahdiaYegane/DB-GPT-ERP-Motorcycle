import { IFlowNodeParameter } from '@/types/flow';
import { convertKeysToCamelCase } from '@/utils/flow';
import { Cascader } from 'antd';

export const renderCascader = (data: IFlowNodeParameter) => {
  const attr = convertKeysToCamelCase(data.ui?.attr || {});

  return <Cascader {...attr} options={data.options} placeholder='لطفاً انتخاب کنید' className='w-full nodrag' />;
};
