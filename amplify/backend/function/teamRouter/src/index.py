# © 2023 Amazon Web Services, Inc. or its affiliates. All Rights Reserved.
# This AWS Content is provided subject to the terms of the AWS Customer Agreement available at
# http: // aws.amazon.com/agreement or other written agreement between Customer and either
# Amazon Web Services, Inc. or Amazon Web Services EMEA SARL or both.
import os
import json
import boto3
import requests
from botocore.exceptions import ClientError
from requests_aws_sign import AWSV4Sign
import asyncio
from botocore.config import Config
    
policy_table_name = os.getenv("POLICY_TABLE_NAME")
settings_table_name = os.getenv("SETTINGS_TABLE_NAME")
approver_table_name = os.getenv("APPROVER_TABLE_NAME")
user_pool_id = os.getenv("AUTH_TEAM06DBB7FC_USERPOOLID")
dynamodb = boto3.resource('dynamodb')
approver_table = dynamodb.Table(approver_table_name)
policy_table = dynamodb.Table(policy_table_name)
settings_table = dynamodb.Table(settings_table_name)

grant = os.getenv("GRANT_SM")
revoke = os.getenv("REVOKE_SM")
reject = os.getenv("REJECT_SM")
schedule = os.getenv("SCHEDULE_SM")
approval = os.getenv("APPROVAL_SM")


def list_account_for_ou(ouId):
    account = []
    client = boto3.client('organizations')
    try:
        p = client.get_paginator('list_accounts_for_parent')
        paginator = p.paginate(ParentId=ouId,)

        for page in paginator:
            for acct in page['Accounts']:
                account.extend([{"name": acct['Name'], 'id':acct['Id']}])
        return account
    except ClientError as e:
        print(e.response['Error']['Message'])


def get_entitlements(id):
    response = policy_table.get_item(
        Key={
            'id': id
        }
    )
    return response


def get_settings():
    response = settings_table.get_item(
        Key={
            'id': 'settings'
        }
    )
    return response

def getEntitlements(userId, groupIds):
    eligibility = []
    maxDuration = 0
    for id in [userId] + groupIds:
        if not id:
            continue
        entitlement = get_entitlements(id)
        if "Item" not in entitlement.keys():
            continue
        duration = entitlement['Item']['duration']
        if int(duration) > maxDuration:
            maxDuration = int(duration)
        policy = {}
        policy['accounts'] = entitlement['Item']['accounts']
        
        for ou in entitlement["Item"]["ous"]:
            data = list_account_for_ou(ou["id"])
            policy['accounts'].extend(data)
            
        policy['permissions'] = entitlement['Item']['permissions']
        policy['approvalRequired'] = entitlement['Item']['approvalRequired']
        policy['duration'] = str(maxDuration)
        
        eligibility.append(policy)

    return eligibility

def list_idc_group_membership(userId):
    try:
        client = boto3.client('identitystore')
        p = client.get_paginator('list_group_memberships_for_member')
        paginator = p.paginate(IdentityStoreId=sso_instance['IdentityStoreId'],
            MemberId={
                'UserId': userId
            })
        all_idc_groups = []
        for page in paginator:
            all_idc_groups.extend(page["GroupMemberships"])
        return all_idc_groups
    except ClientError as e:
        print(e.response['Error']['Message'])
        return []

def updateRequest(input):
    session = boto3.session.Session()
    credentials = session.get_credentials()
    credentials = credentials.get_frozen_credentials()
    region = session.region_name

    input = input
    query = """
        mutation UpdateRequests(
            $input: UpdateRequestsInput!
            $condition: ModelRequestsConditionInput
        ) {
            updateRequests(input: $input, condition: $condition) {
            id
            email
            accountId
            accountName
            role
            roleId
            startTime
            duration
            justification
            status
            comment
            username
            approver
            approverId
            approvers
            approver_ids
            revoker
            revokerId
            endTime
            ticketNo
            revokeComment
            createdAt
            updatedAt
            owner
            }
        }
    """

    endpoint = os.environ.get('API_TEAM_GRAPHQLAPIENDPOINTOUTPUT', None)
    headers = {"Content-Type": "application/json"}
    payload = {"query": query, 'variables': {'input': input}}

    appsync_region = region
    auth = AWSV4Sign(credentials, appsync_region, 'appsync')

    try:
        response = requests.post(
            endpoint,
            auth=auth,
            json=payload,
            headers=headers
        ).json()
        if 'errors' in response:
            print('Error attempting to query AppSync')
            print(response['errors'])
        else:
            print(response)
            return response
    except Exception as exception:
        print('Error with Query')
        print(exception)

    return None


def list_existing_sso_instances():
    client = boto3.client('sso-admin')
    try:
        response = client.list_instances()
        return response['Instances'][0]
    except ClientError as e:
        print(e.response['Error']['Message'])


def get_user(username):
    try:
        client = boto3.client('identitystore')
        response = client.list_users(
            IdentityStoreId=sso_instance['IdentityStoreId'],
            Filters=[
                {
                    'AttributePath': 'UserName',
                    'AttributeValue': username
                },
            ]
        )
        if response['Users']:
            return response['Users'][0]['UserId']
        else:
            return
    except ClientError as e:
        print(e.response['Error']['Message'])


def invoke_approval_sm(request, sm_arn):
    sfn_client = boto3.client('stepfunctions')
    try:
        response = sfn_client.start_execution(
            stateMachineArn=sm_arn,
            name=request["id"],
            input=(json.dumps(request)))
    except ClientError as e:
        print(e.response['Error']['Message'])
    else:
        sfn_arn = response.get('executionArn')
        return sfn_arn


sso_instance = list_existing_sso_instances()


def get_request_data(data, expire, approval_required):
    request = {
        "email": data.get("email", {}).get("S"),
        "username": data["username"]["S"],
        "duration": str(int(data["duration"]["S"]) * 60 * 60),
        "accountId": data["accountId"]["S"],
        "status": data["status"]["S"],
        "accountName": data["accountName"]["S"],
        "id": data["id"]["S"],
        "role": data["role"]["S"],
        "roleId": data["roleId"]["S"],
        "time": data["duration"]["S"],
        "startTime": data["startTime"]["S"],
        "justification": data["justification"]["S"],
        "approver": data.get("approver", {}).get("S"),
        "revoker": data.get("revoker", {}).get("S"),
        "instanceARN": sso_instance['InstanceArn'],
        "approvers": [approver["S"] for approver in data.get("approvers", {}).get("L",[]) if approver["S"] != data.get("email", {}).get("S")],
        "expire": expire,
        "approvalRequired": approval_required
    }
    return request

def eligibility_error(request):
    print("Error - Invalid Eligibility")
    input = {
            'id': request["id"],
            'status': 'error'
            }
    updateRequest(input)
    
def get_eligibility(request, userId):
    eligible = False
    groupIds = [group['GroupId'] for group in list_idc_group_membership(userId)]
    entitlement = getEntitlements(userId=userId, groupIds=groupIds)
    for eligibility in entitlement:
        if int(request["time"]) > int(eligibility["duration"]):
            return eligibility_error(request)
        for account in eligibility["accounts"]:
            if request["accountId"] ==  account["id"]:
                for permission in eligibility["permissions"]:
                    if request["roleId"] == permission["id"]:
                        if eligibility["approvalRequired"]:
                            return {"approval":True}
                        eligible = True
    if eligible:
        return {"approval":False}   
    else:
        return eligibility_error(request)          

def check_settings():
    settings = get_settings()
    item_settings = settings.get("Item", {})
    approval_required = item_settings.get("approval", True)
    expiry = int(item_settings.get("expiry", 3)) * 60 * 60
    max_duration = item_settings.get("duration", "9")
    return {"approval_required": approval_required, "expiry": expiry, "max_duration":max_duration}

        
def invoke_workflow(request, approval_required):
    workflow = None
    if approval_required and request["status"] == "pending":
        print("sending approval")
        workflow = approval
    elif approval_required and request["status"] == "approved" and request["email"] != request["approver"]:
        print("scheduling session")
        workflow = schedule
    elif approval_required and request["status"] == "rejected" and request["email"] != request["approver"]:
        print("rejecting request")
        workflow = reject
    elif request["status"] == "revoked":
        print("revoking session")
        workflow = revoke
    elif request["status"] == "pending" and not approval_required:
        print("scheduling session - approval not required")
        workflow = schedule
    elif request["status"] == "cancelled":
        print("cancelling request")
        workflow = reject
    elif approval_required and request["status"] in ["approved","rejected"] and request["email"] == request["approver"]:
        print("Error: Invalid Approver")
        input = {
                'id': request["id"],
                'status': 'error'
                }
        updateRequest(input)
    else:
        print("no action")
    if workflow:
        invoke_approval_sm(request, workflow)

def get_email(username):
    cognito = boto3.client('cognito-idp', config=Config(user_agent_extra="team-idc"))
    next_page = None
    kwargs = {
        'UserPoolId': user_pool_id,
        "Filter": f"username = \"{username}\"",
        "AttributesToGet": [
            "email"
        ],
    }
    users_remain = True
    while(users_remain):
        if next_page:
            kwargs['PaginationToken'] = next_page
        response = cognito.list_users(**kwargs)
        next_page = response.get('PaginationToken', None)
        users_remain = next_page is not None

    email_id = response['Users'][0]['Attributes'][0]['Value']
    return email_id

def get_ou(id):
    client = boto3.client('organizations')
    try:
        response = client.list_parents(
            ChildId=id
        )
        return response["Parents"][0]
    except ClientError as e:
        print(e.response['Error']['Message'])

async def getPsDuration(ps):
    client = boto3.client('sso-admin')
    response = client.describe_permission_set(
    InstanceArn=sso_instance['InstanceArn'],
    PermissionSetArn=ps
    )
    return response['PermissionSet']['SessionDuration']

def list_approvers(id):
    try:
        response = approver_table.get_item(
            Key={
                'id': id
            }
        )
        if "Item" in response.keys():
            return (response['Item']['groupIds'])
        else:
            return None
    except ClientError as e:
        print(e.response['Error']['Message'])
        
def get_approver_group_ids(accountId):
    try:
        approvers = list_approvers(accountId)
        if approvers:
            return approvers
        else:
            ou = get_ou(accountId)
            return(list_approvers(ou["Id"]))
    except ClientError as e:
        print("no approvers for account " + accountId)

def get_approvers(userId):
    client = boto3.client('identitystore')
    response = client.describe_user(
        IdentityStoreId=sso_instance['IdentityStoreId'],
        UserId=userId
    )
    approver_id = "idc_" + response['UserName']
    for email in response['Emails']:
        if email:
            approver = email["Value"]
            break
    return {"approver_id": approver_id, "approver": approver}

def list_group_membership(groupId):
    try:
        client = boto3.client('identitystore')
        p = client.get_paginator('list_group_memberships')
        paginator = p.paginate(IdentityStoreId=sso_instance['IdentityStoreId'],
        GroupId=groupId,
        )
        all_groups = []
        for page in paginator:
            all_groups.extend(page["GroupMemberships"])
        return all_groups
    except ClientError as e:
        print(e.response['Error']['Message'])
        
async def get_approvers_details(accountId):
    approver_groups = get_approver_group_ids(accountId)
    approvers = []
    approver_ids = []
    if approver_groups:
        for group in approver_groups:
            approvers_data = [get_approvers(result["MemberId"]["UserId"])
              for result in list_group_membership(group)]
            for data in approvers_data:
                if data["approver"] not in approvers:
                    approvers.append(data["approver"])
                    approver_ids.append(data["approver_id"].lower())
    return {"approvers":approvers, "approver_ids":approver_ids}

async def updateRequestDetails(request_id, username, accountId, roleId):
    email = get_email(username)
    approver_details = await get_approvers_details(accountId)
    approver_ids = approver_details["approver_ids"]
    approvers = approver_details["approvers"]
    session_duration = await getPsDuration(roleId)
    
    input = {
        'id': request_id,
        'email': email,
        'approvers': approvers,
        'approver_ids': approver_ids,
        'session_duration': session_duration        
    }
    
    updateRequest(input)

def updateApproverDetails(request_id,username):
    approver = get_email(username)
    input = {
            'id': request_id,
            'approver': approver
            }
    updateRequest(input)

def updateRevokerDetails(request_id,username):
    revoker = get_email(username)
    input = {
            'id': request_id,
            'revoker': revoker
            }
    updateRequest(input)

def request_is_updated(status,data,username,request_id):
    updated = False
    if status in ["error", "ended"]:
        return updated
    elif status == "pending" and "email" not in data.keys():
        asyncio.run(updateRequestDetails(request_id, username, data["accountId"]["S"], data["roleId"]["S"]))
        print("updating request details")
    elif status in ["approved","rejected"] and "approver" not in data.keys():
        updateApproverDetails(request_id,data["approverId"]["S"])
    elif status == "revoked" and "revoker" not in data.keys():
        updateRevokerDetails(request_id,data["revokerId"]["S"])
    else:
        updated = True
    return updated

def handler(event, context):
    data = event["Records"].pop()["dynamodb"]["NewImage"]
    print("Checking if request is updated")
    status = data["status"]["S"]
    username = data["username"]["S"]
    request_id = data["id"]["S"]
    if request_is_updated(status,data,username,request_id):
        settings = check_settings()
        approval_required = settings["approval_required"]
        expiry_time = settings["expiry"]
        request = get_request_data(data, expiry_time, approval_required)
        if int(request["time"]) > int(settings["max_duration"]):
            print("Error: Invalid Duration")
            input = {
                    'id': request["id"],
                    'status': 'error'
                    }
            return updateRequest(input)
        print("Received event: %s" % json.dumps(request))
        userId = get_user((data["username"]["S"])[4:])
        request["userId"] = userId
        eligible = get_eligibility(request, userId)
        if eligible:
            if approval_required:
                approval_required = eligible["approval"]
                request["approvalRequired"] = eligible["approval"]
            invoke_workflow(request, approval_required)
    else:
        print("Request not updated")
        