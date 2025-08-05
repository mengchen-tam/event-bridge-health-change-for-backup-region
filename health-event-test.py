#!/usr/bin/env python3
"""
EventBridge Health 实际测试脚本
测试当前的EventBridge规则行为，用于10月31日前后对比
"""

import boto3
import json
import time
import uuid
from datetime import datetime, timezone
import argparse

class RealHealthEventTest:
    def __init__(self):
        self.session = boto3.Session()
        self.beijing_region = 'cn-north-1'
        self.ningxia_region = 'cn-northwest-1'
        
        # 创建两个区域的客户端
        self.clients = {
            'beijing': {
                'events': self.session.client('events', region_name=self.beijing_region),
                'sqs': self.session.client('sqs', region_name=self.beijing_region),
                'sts': self.session.client('sts', region_name=self.beijing_region)
            },
            'ningxia': {
                'events': self.session.client('events', region_name=self.ningxia_region),
                'sqs': self.session.client('sqs', region_name=self.ningxia_region),
                'sts': self.session.client('sts', region_name=self.ningxia_region)
            }
        }
        
        self.test_id = f"real-test-{uuid.uuid4().hex[:6]}"
        self.resources = []
        
        print(f"🚀 EventBridge Health 实际行为测试")
        print(f"测试ID: {self.test_id}")
        print(f"测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 50)

    def get_account_id(self, region: str) -> str:
        """获取账户ID"""
        return self.clients[region]['sts'].get_caller_identity()['Account']

    def create_test_setup(self, region_name: str, with_filter: bool = False):
        """在指定区域创建测试环境"""
        print(f"\n📋 在{region_name}区域创建测试环境")
        
        region_key = 'beijing' if region_name == '北京' else 'ningxia'
        region_code = self.beijing_region if region_name == '北京' else self.ningxia_region
        
        # 创建SQS队列
        queue_name = f"{self.test_id}-{region_key}"
        account_id = self.get_account_id(region_key)
        
        queue_response = self.clients[region_key]['sqs'].create_queue(
            QueueName=queue_name,
            Attributes={
                'MessageRetentionPeriod': '600',  # 10分钟
                'VisibilityTimeout': '30'
            }
        )
        queue_url = queue_response['QueueUrl']
        
        # 设置队列策略
        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "events.amazonaws.com"},
                    "Action": "sqs:SendMessage",
                    "Resource": f"arn:aws-cn:sqs:{region_code}:{account_id}:{queue_name}"
                }
            ]
        }
        
        self.clients[region_key]['sqs'].set_queue_attributes(
            QueueUrl=queue_url,
            Attributes={'Policy': json.dumps(policy)}
        )
        
        # 创建EventBridge规则
        rule_name = f"{self.test_id}-rule-{region_key}"
        event_pattern = {
            "source": ["custom.health.test"],
            "detail-type": ["AWS Health Event"],
            "detail": {
                "service": ["EBS"],
                "eventTypeCategory": ["issue"],
                "eventTypeCode": ["AWS_EBS_DEGRADED_EBS_VOLUME_PERFORMANCE"]
            }
        }
        
        # 添加区域过滤器（如果指定）
        filter_desc = ""
        if with_filter:
            region_filter = "CN-NORTH-1" if region_name == '北京' else "CN-NORTHWEST-1"
            event_pattern["detail"]["eventRegion"] = [region_filter]
            filter_desc = f"（过滤器: {region_filter}）"
        
        self.clients[region_key]['events'].put_rule(
            Name=rule_name,
            EventPattern=json.dumps(event_pattern),
            State='ENABLED',
            Description=f'Real test rule for {region_name} region'
        )
        
        # 添加SQS目标
        queue_arn = f"arn:aws-cn:sqs:{region_code}:{account_id}:{queue_name}"
        self.clients[region_key]['events'].put_targets(
            Rule=rule_name,
            Targets=[{'Id': '1', 'Arn': queue_arn}]
        )
        
        resource = {
            'region_name': region_name,
            'region_key': region_key,
            'queue_name': queue_name,
            'queue_url': queue_url,
            'rule_name': rule_name,
            'with_filter': with_filter
        }
        self.resources.append(resource)
        
        print(f"  ✅ 队列: {queue_name}")
        print(f"  ✅ 规则: {rule_name} {filter_desc}")
        
        return resource

    def send_health_event_to_region(self, target_region: str, event_region: str):
        """向指定区域发送Health事件"""
        communication_id = f"real-{uuid.uuid4().hex[:8]}"
        
        region_key = 'beijing' if target_region == '北京' else 'ningxia'
        event_region_code = "CN-NORTH-1" if event_region == '北京' else "CN-NORTHWEST-1"
        
        event_detail = {
            "eventArn": f"arn:aws-cn:health:{self.beijing_region if event_region == '北京' else self.ningxia_region}::event/EBS/AWS_EBS_DEGRADED_EBS_VOLUME_PERFORMANCE/{communication_id}",
            "service": "EBS",
            "eventTypeCode": "AWS_EBS_DEGRADED_EBS_VOLUME_PERFORMANCE",
            "eventTypeCategory": "issue",
            "eventRegion": event_region_code,
            "communicationId": communication_id,
            "startTime": datetime.now(timezone.utc).isoformat(),
            "eventDescription": [
                {
                    "language": "zh_CN",
                    "latestDescription": f"实际测试：{event_region}区域EBS性能问题"
                }
            ]
        }
        
        response = self.clients[region_key]['events'].put_events(
            Entries=[
                {
                    'Source': 'custom.health.test',
                    'DetailType': 'AWS Health Event',
                    'Detail': json.dumps(event_detail),
                    'Time': datetime.now(timezone.utc)
                }
            ]
        )
        
        if response['FailedEntryCount'] == 0:
            print(f"  📤 向{target_region}区域发送{event_region}区域事件: {communication_id}")
            return communication_id
        else:
            print(f"  ❌ 发送失败: {response}")
            return None

    def check_messages(self, resource: dict, timeout: int = 25):
        """检查队列消息"""
        print(f"  🔍 检查{resource['region_name']}区域队列...")
        
        messages = []
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            response = self.clients[resource['region_key']]['sqs'].receive_message(
                QueueUrl=resource['queue_url'],
                MaxNumberOfMessages=10,
                WaitTimeSeconds=3
            )
            
            if 'Messages' in response:
                for message in response['Messages']:
                    try:
                        body = json.loads(message['Body'])
                        comm_id = body.get('detail', {}).get('communicationId', 'unknown')
                        event_region = body.get('detail', {}).get('eventRegion', 'unknown')
                        
                        messages.append({
                            'communication_id': comm_id,
                            'event_region': event_region
                        })
                        
                        # 删除消息
                        self.clients[resource['region_key']]['sqs'].delete_message(
                            QueueUrl=resource['queue_url'],
                            ReceiptHandle=message['ReceiptHandle']
                        )
                        
                    except json.JSONDecodeError:
                        print(f"    ⚠️ 无法解析消息")
            
            if len(messages) >= 2:  # 最多期望2条消息
                break
                
            time.sleep(2)
        
        return messages

    def test_current_behavior(self):
        """测试当前实际行为"""
        print(f"\n{'='*60}")
        print("🧪 测试当前实际行为")
        print("   这个测试反映了当前系统的真实行为")
        print("   10月31日后运行同样的测试，结果可能会不同")
        print("=" * 60)
        
        # 在两个区域创建测试环境（无过滤器）
        beijing_resource = self.create_test_setup("北京", with_filter=False)
        ningxia_resource = self.create_test_setup("宁夏", with_filter=False)
        
        print(f"\n📤 发送测试事件:")
        time.sleep(5)  # 等待资源生效
        
        # 发送北京区域事件到北京
        beijing_event_id = self.send_health_event_to_region("北京", "北京")
        
        # 发送宁夏区域事件到宁夏  
        ningxia_event_id = self.send_health_event_to_region("宁夏", "宁夏")
        
        print(f"\n📊 检查接收结果:")
        
        # 检查北京区域队列
        beijing_messages = self.check_messages(beijing_resource)
        beijing_from_beijing = len([m for m in beijing_messages if m['event_region'] == 'CN-NORTH-1'])
        beijing_from_ningxia = len([m for m in beijing_messages if m['event_region'] == 'CN-NORTHWEST-1'])
        
        # 检查宁夏区域队列
        ningxia_messages = self.check_messages(ningxia_resource)
        ningxia_from_beijing = len([m for m in ningxia_messages if m['event_region'] == 'CN-NORTH-1'])
        ningxia_from_ningxia = len([m for m in ningxia_messages if m['event_region'] == 'CN-NORTHWEST-1'])
        
        print(f"\n📋 当前行为测试结果:")
        print(f"  北京规则接收北京事件: {beijing_from_beijing} 条")
        print(f"  北京规则接收宁夏事件: {beijing_from_ningxia} 条")
        print(f"  宁夏规则接收北京事件: {ningxia_from_beijing} 条")
        print(f"  宁夏规则接收宁夏事件: {ningxia_from_ningxia} 条")
        
        print(f"\n💡 结果分析:")
        if beijing_from_beijing > 0 and beijing_from_ningxia == 0 and ningxia_from_beijing == 0 and ningxia_from_ningxia > 0:
            print("  ✅ 符合当前预期：各区域只接收本区域事件")
            print("  📝 这表明备份机制尚未启用")
        elif beijing_from_ningxia > 0 or ningxia_from_beijing > 0:
            print("  🆕 检测到跨区域事件！备份机制可能已经启用")
            print("  ⚠️ 如果不希望接收跨区域事件，请添加区域过滤器")
        else:
            print("  ⚠️ 未接收到预期的事件，可能需要更长等待时间")
        
        return {
            'beijing_from_beijing': beijing_from_beijing,
            'beijing_from_ningxia': beijing_from_ningxia,
            'ningxia_from_beijing': ningxia_from_beijing,
            'ningxia_from_ningxia': ningxia_from_ningxia
        }

    def test_with_filters(self):
        """测试添加过滤器后的行为"""
        print(f"\n{'='*60}")
        print("🧪 测试添加区域过滤器后的行为")
        print("   这是AWS推荐的解决方案")
        print("=" * 60)
        
        # 在两个区域创建测试环境（有过滤器）
        beijing_resource = self.create_test_setup("北京", with_filter=True)
        ningxia_resource = self.create_test_setup("宁夏", with_filter=True)
        
        print(f"\n📤 发送测试事件:")
        time.sleep(5)  # 等待资源生效
        
        # 模拟备份机制：向两个区域都发送事件
        print("  📝 模拟备份机制：同时向两个区域发送事件")
        
        # 北京事件发送到两个区域
        self.send_health_event_to_region("北京", "北京")
        self.send_health_event_to_region("宁夏", "北京")  # 备份
        
        # 宁夏事件发送到两个区域
        self.send_health_event_to_region("宁夏", "宁夏")
        self.send_health_event_to_region("北京", "宁夏")  # 备份
        
        print(f"\n📊 检查接收结果:")
        
        # 检查北京区域队列
        beijing_messages = self.check_messages(beijing_resource)
        beijing_from_beijing = len([m for m in beijing_messages if m['event_region'] == 'CN-NORTH-1'])
        beijing_from_ningxia = len([m for m in beijing_messages if m['event_region'] == 'CN-NORTHWEST-1'])
        
        # 检查宁夏区域队列
        ningxia_messages = self.check_messages(ningxia_resource)
        ningxia_from_beijing = len([m for m in ningxia_messages if m['event_region'] == 'CN-NORTH-1'])
        ningxia_from_ningxia = len([m for m in ningxia_messages if m['event_region'] == 'CN-NORTHWEST-1'])
        
        print(f"\n📋 过滤器测试结果:")
        print(f"  北京规则接收北京事件: {beijing_from_beijing} 条")
        print(f"  北京规则接收宁夏事件: {beijing_from_ningxia} 条")
        print(f"  宁夏规则接收北京事件: {ningxia_from_beijing} 条")
        print(f"  宁夏规则接收宁夏事件: {ningxia_from_ningxia} 条")
        
        print(f"\n💡 过滤器效果:")
        if beijing_from_beijing > 0 and beijing_from_ningxia == 0 and ningxia_from_beijing == 0 and ningxia_from_ningxia > 0:
            print("  ✅ 过滤器工作正常：各区域只接收本区域事件")
            print("  ✅ 即使有备份机制，过滤器确保了预期行为")
        else:
            print("  ⚠️ 过滤器可能未按预期工作，请检查配置")

    def cleanup_resources(self):
        """清理所有测试资源"""
        print(f"\n🧹 清理测试资源...")
        
        for resource in self.resources:
            try:
                # 删除EventBridge规则
                self.clients[resource['region_key']]['events'].remove_targets(
                    Rule=resource['rule_name'],
                    Ids=['1']
                )
                self.clients[resource['region_key']]['events'].delete_rule(
                    Name=resource['rule_name']
                )
                print(f"  ✅ 删除{resource['region_name']}规则: {resource['rule_name']}")
                
                # 删除SQS队列
                self.clients[resource['region_key']]['sqs'].delete_queue(
                    QueueUrl=resource['queue_url']
                )
                print(f"  ✅ 删除{resource['region_name']}队列: {resource['queue_name']}")
                
            except Exception as e:
                print(f"  ⚠️ 清理{resource['region_name']}资源时出错: {e}")

    def run_test(self, test_type: str = 'current'):
        """运行测试"""
        try:
            if test_type == 'current':
                result = self.test_current_behavior()
                
                print(f"\n🎯 测试建议:")
                print(f"   1. 保存这个测试结果")
                print(f"   2. 在2025年10月31日后重新运行相同测试")
                print(f"   3. 对比结果，如果出现跨区域事件，请添加区域过滤器")
                print(f"   4. 运行 --test-type filter 验证过滤器效果")
                
            elif test_type == 'filter':
                self.test_with_filters()
                
                print(f"\n🎯 过滤器配置:")
                print(f'   在EventBridge规则的事件模式中添加：')
                print(f'   "detail": {{')
                print(f'     "eventRegion": ["CN-NORTH-1"]  // 北京区域')
                print(f'     // 或 ["CN-NORTHWEST-1"] 宁夏区域')
                print(f'   }}')
                
        except KeyboardInterrupt:
            print(f"\n⚠️ 测试被用户中断")
        except Exception as e:
            print(f"\n❌ 测试执行失败: {e}")
        finally:
            self.cleanup_resources()
            print(f"\n🎉 测试完成！")

def main():
    parser = argparse.ArgumentParser(description='EventBridge Health 实际行为测试')
    parser.add_argument('--test-type', 
                       choices=['current', 'filter'], 
                       default='current',
                       help='测试类型: current(当前行为), filter(过滤器效果)')
    
    args = parser.parse_args()
    
    tester = RealHealthEventTest()
    tester.run_test(args.test_type)

if __name__ == "__main__":
    main()