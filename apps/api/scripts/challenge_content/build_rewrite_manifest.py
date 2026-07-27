#!/usr/bin/env python3
"""Build the reviewed v1 manifest from compact, deterministic authoring data.

This generator is offline: it does not connect to the database, executor, or an
AI provider. Source fingerprints were produced by the read-only fingerprint
function in ``repair_challenge_content.py`` and deliberately contain no source,
answers, test inputs, or personal data.
"""

from __future__ import annotations

import json
from pathlib import Path


PYTHON_COURSE = "course_ed36690c-ac73-4136-95bb-df9ce01fd6be"
JS_COURSE = "course_9761a1d6-5caa-4f05-bf90-9f254f578026"

# activity_uuid, challenge_uuid, block_id, source_fingerprint
PYTHON_IDENTITIES = [
    ("activity_a2f2e0bf-5e05-43d9-9fd3-8f25aca9e13c", "challenge_d0fa5997-9dfb-43f5-97bb-951df0dfdb94", "block_592b768f-0bae-4f46-ab57-46aff5549777", "sha256:afdaa67aeeaee2ff57ab956018069dd21f0f7acf314180ee7b4030307e4abd3e"),
    ("activity_6769b5b6-58c0-4f4e-bcc0-394df2b6c151", "challenge_a66626f6-15db-4ad0-95b2-a8be17f2215c", "block_f924d166-caad-4b07-92ba-1e92c05f175f", "sha256:efdaa77061ab76a04ebe6e21250abbe0796112a4952964209a2db3b2a5475cb1"),
    ("activity_3f27ff5c-3c04-4eb0-93c4-4c10fcce2c69", "challenge_09c20cda-3829-4f86-8dba-ce4dc6bc4608", "block_4038ad63-a951-4a6a-85e9-6035dcce176a", "sha256:6672e632dda82971b140c4fedefe5cc161882288c7704ba33711dd658514c418"),
    ("activity_539bfe9c-2def-4b5b-b3fc-97ab853b78f5", "challenge_135ab329-ab96-4308-93e1-5550d98d7100", "block_7b0b25cb-5f71-4ba7-8849-595efd27f734", "sha256:d8b89aa81d1840ce0e183a639b56f0e3504c713a0aa2045309ae716cd76b3ac3"),
    ("activity_9412b254-44d1-42c7-9e95-8ceb4a8d584a", "challenge_5b5fd15e-dadb-424f-a231-20f2cc4d577b", "block_79d91835-6320-4d7a-9413-b4067902b24b", "sha256:550ac62bb32ccc149530a03fdd03744af0b90de4a42e4ee10c03cbcac10c7fc0"),
    ("activity_21eaa078-2164-4434-bc18-96e5b9c7dab8", "challenge_6c68f3d8-4dc7-4acc-a4e2-7f0a5c0abcc2", "block_b30f2100-5bea-41df-816d-17a691b4789a", "sha256:b6cae6091604f1380db8437ee435b3fff48864e3c0cb9e1a00d9f2d504195dd4"),
    ("activity_49e85035-01f8-49aa-b92e-97314d8802f7", "challenge_ca872ab0-8bab-4ae4-889e-9fe5ded81a8a", "block_e5c493c4-44e1-4747-ad10-b8642a198601", "sha256:34389a129ef69418bb57fca5799d8c03f2f705fae964ecd4f22ffd6b7d1c554f"),
    ("activity_0ea657e6-6ee8-4c6d-a26d-d01b29d21f84", "challenge_4e40d714-fa8d-458d-8e2e-554d83ede9fd", "block_1a525a59-ef41-4c38-9d08-ee964d93b702", "sha256:70220fe07fbdb6910f894823bca5c56421ef2fcb5edf053abee82f0175922b73"),
    ("activity_02dca35c-9b44-4593-b3a3-a36cdb4861ef", "challenge_3cad5de7-a521-4c29-bb4a-2088cdf49a35", "block_f6edd6a0-d766-4e97-8731-0712220bf5de", "sha256:3bc9168e3d94006786184d74651bd23c2b10cf6777b30229015b5ddbd8d2fd7c"),
    ("activity_bbc967bc-3ae2-43a0-95e4-570765e1087c", "challenge_5e14638a-bfd0-4a6a-a8ae-71704a35cbfa", "block_48693739-b01a-456b-80a1-2d5d65b1bccb", "sha256:56a99d963084bcfc5046984f48f1c1666c645ddaca1e655366571b67f10773a8"),
    ("activity_116473e9-0391-47c3-9e13-b32211e72f58", "challenge_b0abe61d-e611-4621-9e60-bf1217bf910f", "block_0552f5eb-276e-4c69-abb2-23c829e832b4", "sha256:218187f216f61fe356cc27f11f10a21aa256c08951cc049658d0952ce7b75c13"),
]

JS_IDENTITIES = [
    ("activity_335b7daf-0152-464d-b768-ee37b0f3f307", "challenge_d5361b43-b766-4775-8d92-17c77ed7fa21", "block_693986e7-4502-4a00-a3c8-5e7e4ccf1436", "sha256:0ab93a3316125d425eba0359eb83cf69f7028ae4ebe3269db84a74232ba86122"),
    ("activity_5d372272-3dd9-4f14-85e8-68801dfe88d0", "challenge_d9881718-95bd-4eae-a613-fdf11636c61b", "block_50eddbc1-80db-4de3-86ca-7c5be51253e3", "sha256:fc006afda2dff6b31b9057e3e0e0b5cbaaab686fba951376494776c6261b7d16"),
    ("activity_c9f3081f-77d1-40f1-b4ce-cbd81946bfe4", "challenge_a48093ee-42e0-47da-a361-c5cc13b9a90d", "block_37729a81-89a9-4464-a45b-2267488d3284", "sha256:a9536241279461d46a3d3d259b6b8007bed9a952a4e7bc2740c764ad5de69ccf"),
    ("activity_d4eb4aff-f0f9-470c-b8e5-518070519e7c", "challenge_44475efd-c5c6-4c61-9a0e-f4761ad3de72", "block_37b4f483-f32b-45fd-8695-2d79ff4db07c", "sha256:921f2891656f7ed84ef153dc04bbfd7334df883663ce718730da557ee7f13df5"),
    ("activity_bd2eb9a3-8ef2-480e-af86-246737e7d7e1", "challenge_0b62bd6b-570e-44dd-84e3-a9950791d792", "block_dc879fed-571c-4cc3-bb23-a6fd658c41b4", "sha256:be470930adb3811ab82ba0c438ed3d7adfe64d5a80717f86245e2e4e852facc5"),
    ("activity_c0819c47-771b-4cba-9ebc-85de380981a7", "challenge_b9c4a670-0f24-41f8-9fab-f2a0d294aef7", "block_ce7ca2cd-c069-41dc-984c-9aea13eb4b79", "sha256:2f17f506c8feaa9e736f21c71f357ab72c76b1e1ee9df5fde4f297862d1bf199"),
    ("activity_73745b31-0d92-4ea3-b13e-178f7d9f3b69", "challenge_7ac593a1-f9c3-42d5-ac6d-2759a46a6635", "block_cdbfb6d5-50f1-421c-82bc-33efccd8a6ed", "sha256:424bb0b0cbc7b3be57b2aac9a7d72af55385ae81e96c1da37af96c9f3cd84335"),
    ("activity_45958f18-6fbf-4ae7-8bd8-42e454c50899", "challenge_7bb0a57b-429a-4f92-8168-fc6cfd39aaa9", "block_1c8f16d0-d4ff-44a5-ad2f-086cb1360168", "sha256:935c9bd55a84fa1a9ce73bf993fa559cd6a9927c49de26e2ae8546d64dc67f15"),
    ("activity_887804bb-3fb1-4c52-8f2c-f94aad36d5b9", "challenge_53e64864-5718-4a41-bc10-4f7b2a18f2a2", "block_61b68c97-49e7-4dc7-89b9-be12306539a0", "sha256:616e6f0aff5c52308bfad87270cbded06886087c83cad6c26274b597fc7b174a"),
    ("activity_74d467be-a7ed-46c5-aeb6-f14fa334aed3", "challenge_17ea78dd-4a78-4094-b5a0-4fe6b2b22c27", "block_b9b5c784-d11d-4108-82f7-21aca27b3c64", "sha256:c4b5716478cc118360e9a2e3bfd9282c775d622d03a321793703208ef8636dcf"),
    ("activity_d92caddf-d692-4bcd-95ba-468fa5defac4", "challenge_f0bde073-79a8-47d8-b514-2e65329ff24b", "block_3979b344-30d6-4cda-89cf-47d06deaec43", "sha256:ca0cae50faf5927b0e359c18b09bc8b2fbd23654849abe73c55ef56040053eda"),
    ("activity_bd599a0a-db4f-4011-8a6d-7a53b736f99a", "challenge_922b2aec-2ee8-4f0c-b632-865355960fd5", "block_dd952404-6d7a-46e0-8523-db064902625d", "sha256:950b05d11c5da03eec20df8bd05061b6cea6da683b93212fea9887de176478b5"),
    ("activity_bb25a67d-3ddd-42cf-9e6d-048b1fff108f", "challenge_99e2807b-cab1-4ed5-85e3-32b43b031f2c", "block_fbc89e8c-6304-4060-8384-c3eaf2a2eca7", "sha256:a34e1ab329ede3aa1e1d26ac6315f273cccc4249705c06342473dd849551d1a2"),
    ("activity_37f835b7-9bce-4f45-a8a7-c960795c2e78", "challenge_02fd381a-763b-4c87-9623-f021b64cbb0e", "block_e81bf2ff-7cc9-4c5d-8d79-02ef9229af2d", "sha256:4f44f9c7173c3e9407b75522a99b874e0ac0c3aef145be2ede1544b928764552"),
    ("activity_a4eec52a-f2f5-497d-b90e-a10c7aa51f51", "challenge_c74918f5-251f-4868-9dee-12de4f85518b", "block_241aa6a5-69ee-41e2-8bb1-426592c73173", "sha256:6ab5b8881bd1bd57b9877629004aa8bdb88a2fb8978b8d6ea875b39bf35b4899"),
    ("activity_9621b008-857c-46a6-809d-463e68b3d41c", "challenge_e9bf4dd4-b2a6-448c-bae3-c43f50af474a", "block_5951b8b6-06ef-444f-959d-ad2c4a6c395f", "sha256:d8bac14dae19d8185711c902421615f012c9c4fc463a39bd9d523555fb515d90"),
    ("activity_3afb24bd-b3f3-4cd3-aa92-69bf91329842", "challenge_24d561e5-740d-459b-92a4-fd06ddcf433b", "block_5104b697-1cae-4dcd-a31d-3ff36e9c8cb9", "sha256:dc285ba9957017410ade8022e196f7ad2e2ab83ae9b78042f36529e35a80e059"),
    ("activity_6a05b88e-080a-4697-be01-1e9d084e0993", "challenge_b8068255-5372-4a1c-96a1-fcc4b8d37f84", "block_1cb278f0-e9e1-4ce4-a8fa-f73bd463d36d", "sha256:e08852d0b113f7b470afce466c0d72d1772cbda99af4edd82fd1a72223ab0512"),
    ("activity_0e8af3b2-600a-4187-aac3-9f3dc5c52e37", "challenge_5e22efd0-3676-4391-9b62-722d099bac56", "block_e5dd6a71-8d48-4c37-89f7-14ebf35db0b1", "sha256:ad26f823e69e07a267d14f155dce700dec9a02d88c2d2499db5601d911c4e2d6"),
    ("activity_78e563e7-c0dc-4e2e-92ba-9c49719f1c7c", "challenge_18992a1a-b19c-4d23-81fd-924347d63f37", "block_31bc2f0e-158b-47cf-b631-fbb124b18ea0", "sha256:97aaf58e663b1c33e3718acd5f62d7945e00a4270dc5de937ecf7b80ef82af6e"),
    ("activity_0a1155b5-2253-42ef-9185-5a3ceb26a6b2", "challenge_8e768d78-e2d6-4f87-af5b-aba3ab81d7ec", "block_a366ed02-c0f2-4960-a5ef-68c91a78ffd2", "sha256:ccd53ec0a382b577b0ad9ac2c798392ee6ec269f479e63f3dd30887fc81daaf6"),
    ("activity_f305204f-4394-4ea4-80b2-18bce5d5fc4f", "challenge_f874efbd-4f55-4854-a6c3-fac467c709c0", "block_647b3bb0-c04e-487a-b7ee-df66541311da", "sha256:4b3c201532fee08724f63f0f79a177ba79d36f07b8856101d2d335615e53f254"),
    ("activity_86ead8df-602f-4bf2-a15c-08b374c466dd", "challenge_a5f6b1b0-5c2c-414a-a7d3-cfb6e6bc725e", "block_ca55be6f-dc00-4a21-b020-7bbb60291ca8", "sha256:120d0c8b50255c777608071dc85dbc92ed7c8fab1f24a9eba40c8848417ea4ff"),
    ("activity_6d45d949-b68f-446b-a398-428a5016d15b", "challenge_7d01e25b-bade-44ec-b412-94f320538fc4", "block_fc57040b-fb9a-402d-93fa-5f95305624e5", "sha256:39b0feb50c124a5adb702b118b43a7d30bf50ca6e41fccd490bb7836b3a08043"),
    ("activity_96c3d995-40a5-4afa-aac4-43cba9894f7a", "challenge_5da834b4-8ced-47ca-add7-2565236b742c", "block_63818b4a-ff17-4ad8-921a-4f1aced2c0b0", "sha256:46baf097fc625b5e06a0528d290bcf64600ad14327b79a2300b61054dd4800e9"),
    ("activity_774e3ee4-c1ad-46b4-bf00-d4398a99b3d4", "challenge_19c53a29-4a05-4456-ac56-70ff74fb8669", "block_8d241d2a-8acc-4c78-8f34-0ec8fa444021", "sha256:605c33c166da1f2b9c8617cda121c71720395856a1cb5f23b101c28850f13dbe"),
    ("activity_27351ee7-82cb-40d7-a2cb-9d227aa63beb", "challenge_c41c303a-e04b-4cb1-9c5d-d809d66d424c", "block_1717980b-7949-4d6a-99d1-c7813df06031", "sha256:04bce1060e0266ba36516194b4b4184e931c19e936633bf3f6cf7c64a92a2084"),
    ("activity_6f1f5e8d-f89a-44c7-aaa7-4767173fda29", "challenge_94eafa2a-980b-44a3-90a5-e027cf18de97", "block_1e08f46a-a326-49c3-b673-06db9f9eeea1", "sha256:66a8c427aab4f3be80545fc2c22616ca90fc53ea0630d60e667f6499253ddd44"),
    ("activity_28fa6b3e-0c45-45a0-82d1-979b8a7be67f", "challenge_772ccebb-1e67-4091-9bcc-0167074ceffe", "block_0d904f68-9e4b-4e65-a892-0bb2b134b6bd", "sha256:e4b33aca6c645e7260ddb6f4428c9d4b88aa6ee926ad55fff5b791caa9932372"),
    ("activity_16237614-c1bf-47cc-922a-646dd6e9e6c0", "challenge_ecb942cd-01ff-4501-903e-b0cbc003e4f8", "block_f58ff3f4-7a74-40b7-b5bc-9999def5e0fd", "sha256:43e8d44b58a0c6c1e550444d3ed76b20a8aaeda2c2e7eb8f646a7014b8308e2d"),
    ("activity_524f180c-ef4c-49ce-9f4c-1e26426f9a47", "challenge_a8c199ad-8298-4929-bab5-609baf629c90", "block_12c6f8e9-6b8d-43aa-9d80-02614429582a", "sha256:8ac3321fec568c2759cea1eb7086f6f191810c6355815affc0473e21a3d082ca"),
    ("activity_54d227a4-29b8-4e32-b1e2-8d8a3cbc27db", "challenge_c361ec43-2955-4142-8f21-d7da8272f5c3", "block_df0ff7f3-e23c-4b32-8cc6-e8c41c204316", "sha256:7420fd35315c28bc36aa2ee034fe9ee15069b16e80981a178d19d397bd043998"),
    ("activity_f55da2a5-5e36-4286-9246-429520a561c8", "challenge_8f7329db-8d4e-41ce-bb54-5857eca6fe92", "block_71e2256e-eda9-4645-824b-aa9e69d8dba4", "sha256:bccabc89dff491291226379603ac8dd0bcff592468cf68adc2e6a7184cd5374d"),
    ("activity_748300ff-52e6-487b-821a-3dadd7227b5f", "challenge_2543ea71-eefc-435f-bf7a-4e128d020d8f", "block_bf209bb5-cba1-47c8-82df-15024c2dfb3a", "sha256:f410408d4d09633a66748f56115fe66f2eedbbb68b0b6fe3c013687cdd64ebfa"),
    ("activity_f169bed0-2f19-40db-abd2-feaf06806765", "challenge_cf30528c-8974-46a3-ba41-4ac68b647516", "block_36e2c039-d926-4961-a04f-9493c37ecd9c", "sha256:837b69f872ebbb6130930664aeff043661026421f3c4d3800ddf42baeaee79a0"),
    ("activity_9eccc0a0-3e81-41a1-a9c0-cba40a473d2d", "challenge_3cb20e7b-0553-4b37-bc17-7a34013f36d3", "block_f9629b3c-b2fb-40ac-b828-31d5c89923a0", "sha256:4694824316b60b78060864886babe75cd8c64d8fa98c6638fa1ba7b3550dea8f"),
    ("activity_496cd2ea-8e04-4e5b-9e15-d4b4350e6ef0", "challenge_06fe3bfc-c8d3-45cc-aec8-941189ae8fb1", "block_d609e0c9-7fcf-4cc9-87be-728268a6abcc", "sha256:598eb2f6a2be2bc1251d7020699760fe89a7b04a15a9a622e31573238f614774"),
    ("activity_31e27242-816e-4c64-8f0f-48d89ccb0286", "challenge_95b5a056-b51f-49bb-8e9a-a75cdb64b8f8", "block_5cfcf31b-c974-4c5e-b8e8-7ad5f6050ee5", "sha256:396bae1118398651a05822ff50cb0bfb3d7fee466ed4c0c81317b0c096ae8edd"),
    ("activity_adffc32e-6c5a-481f-b61c-1d689f6fe1ee", "challenge_2496aafc-afad-477e-b5a8-053153085a74", "block_e9ac6fbb-eb10-4fa2-8392-679f5f629211", "sha256:c86ed0aa39af3d7381b19a614aaac143adf70420bbe5058d6408d8c57215eacf"),
    ("activity_123a7506-04a6-4473-ba9a-0ad12dcbac07", "challenge_1f4dc54f-60fd-4556-a958-66e66db2db13", "block_4dda13f4-6589-4ae0-8ee2-4f2d33aec445", "sha256:d635b3efd3be6f5783f5ad258e19b52e4620fcd9bd938d0391f35c25dd071f08"),
]


def task(
    title,
    input_description,
    output_description,
    solution,
    cases,
    hints,
    *,
    difficulty="easy",
):
    return {
        "title": title,
        "description": (
            f"{input_description}\n\n輸入：{input_description}\n"
            f"輸出：{output_description}"
        ),
        "solution_code": solution,
        "cases": cases,
        "hints": hints,
        "difficulty": difficulty,
    }


PYTHON_TASKS = [
    task(
        "依輸入順序輸出兩項計劃", "讀取兩行計劃文字。", "依原有順序輸出兩行。",
        "import sys\nlines = sys.stdin.read().splitlines()\nprint(lines[0])\nprint(lines[1])\n",
        [("學習 Python\n完成練習\n", "學習 Python\n完成練習\n"), ("先觀察\n再動手\n", "先觀察\n再動手\n"), ("測試程式\n分享成果\n", "測試程式\n分享成果\n")],
        ["用 splitlines() 取得每一行。", "按照索引 0、1 的順序輸出。"],
    ),
    task(
        "為檔案清單加上編號", "讀取一行以逗號分隔的檔名。", "由 1 開始，每行輸出「編號. 檔名」。",
        "names = [item.strip() for item in input().split(',')]\nfor index, name in enumerate(names, 1):\n    print(f'{index}. {name}')\n",
        [("index.html,style.css,app.js\n", "1. index.html\n2. style.css\n3. app.js\n"), ("main.py,README.md\n", "1. main.py\n2. README.md\n"), ("data.csv,chart.py,notes.txt,logo.png\n", "1. data.csv\n2. chart.py\n3. notes.txt\n4. logo.png\n")],
        ["先用逗號切開字串。", "enumerate(..., 1) 可由 1 開始編號。"],
    ),
    task(
        "更新變量中的學習分鐘", "讀取兩個整數：原有分鐘與新增分鐘。", "輸出重新賦值後的總分鐘。",
        "current, added = map(int, input().split())\ncurrent = current + added\nprint(current)\n",
        [("25 15\n", "40\n"), ("0 30\n", "30\n"), ("95 25\n", "120\n")],
        ["用 map(int, ...) 轉成整數。", "把相加結果重新存回原來的變量。"],
    ),
    task(
        "判斷文字代表的資料類型", "讀取一行文字，依序嘗試把它解析為布林值、整數或浮點數；若都不符合就視為一般字串。", "輸出該段文字所代表的 int、float、bool 或 str。",
        "value = input().strip()\nif value.lower() in {'true', 'false'}:\n    print('bool')\nelif value.lstrip('-').isdigit():\n    print('int')\nelse:\n    try:\n        float(value)\n        print('float')\n    except ValueError:\n        print('str')\n",
        [("42\n", "int\n"), ("-3.5\n", "float\n"), ("false\n", "bool\n"), ("Macau\n", "str\n")],
        ["input() 取得的原始資料一定是字串，本題判斷的是文字可解析成哪種值。", "先處理布林值與整數；float() 失敗時才判為一般文字。"],
    ),
    task(
        "使用 range 完成倒數", "讀取一個正整數 n。", "由 n 倒數至 1，每個數字一行，最後輸出「開始」。",
        "n = int(input())\nfor number in range(n, 0, -1):\n    print(number)\nprint('開始')\n",
        [("3\n", "3\n2\n1\n開始\n"), ("1\n", "1\n開始\n"), ("5\n", "5\n4\n3\n2\n1\n開始\n")],
        ["range 的終點不會包含在結果內。", "步長 -1 代表每次減一。"],
    ),
    task(
        "整理一筆學習記錄", "讀取「科目,分鐘,完成題數」。", "輸出「科目｜分鐘分鐘｜完成題數題」。",
        "subject, minutes, completed = [item.strip() for item in input().split(',')]\nprint(f'{subject}｜{minutes}分鐘｜{completed}題')\n",
        [("數學,30,8\n", "數學｜30分鐘｜8題\n"), ("英文,45,12\n", "英文｜45分鐘｜12題\n"), ("電腦,20,5\n", "電腦｜20分鐘｜5題\n")],
        ["把一行拆成三個變量。", "使用 f-string 組合固定格式。"],
    ),
    task(
        "修正名稱後計算總分", "讀取兩個整數分數。", "輸出兩個分數的總和。",
        "score_one, score_two = map(int, input().split())\ntotal_score = score_one + score_two\nprint(total_score)\n",
        [("40 35\n", "75\n"), ("0 18\n", "18\n"), ("67 28\n", "95\n")],
        ["兩次使用同一個正確的變量名稱。", "先計算，再輸出 total_score。"],
    ),
    task(
        "建立作品展示摘要", "讀取兩行：作品名稱與目前狀態。", "分兩行輸出「作品：名稱」及「狀態：狀態」。",
        "name = input().strip()\nstatus = input().strip()\nprint(f'作品：{name}')\nprint(f'狀態：{status}')\n",
        [("溫度換算器\n已完成\n", "作品：溫度換算器\n狀態：已完成\n"), ("單字卡\n測試中\n", "作品：單字卡\n狀態：測試中\n"), ("計時器\n待改進\n", "作品：計時器\n狀態：待改進\n")],
        ["每次 input() 讀一行。", "兩個標籤都使用全形冒號。"],
    ),
    task(
        "把項目任務排成步驟", "讀取一行以逗號分隔的任務。", "每行輸出「步驟編號：任務」。",
        "steps = [item.strip() for item in input().split(',')]\nfor index, step in enumerate(steps, 1):\n    print(f'步驟{index}：{step}')\n",
        [("設計,編寫,測試\n", "步驟1：設計\n步驟2：編寫\n步驟3：測試\n"), ("觀察,記錄\n", "步驟1：觀察\n步驟2：記錄\n"), ("構思,製作,檢查,分享\n", "步驟1：構思\n步驟2：製作\n步驟3：檢查\n步驟4：分享\n")],
        ["先建立 steps 清單。", "把編號與內容放進同一個 f-string。"],
    ),
    task(
        "用函數產生選單", "讀取一行以逗號分隔的選單項目。", "呼叫函數，每行輸出「[編號] 項目」。",
        "def show_menu(items):\n    for index, item in enumerate(items, 1):\n        print(f'[{index}] {item}')\n\nitems = [item.strip() for item in input().split(',')]\nshow_menu(items)\n",
        [("新增,查看,離開\n", "[1] 新增\n[2] 查看\n[3] 離開\n"), ("開始,設定\n", "[1] 開始\n[2] 設定\n"), ("課程,練習,成績,登出\n", "[1] 課程\n[2] 練習\n[3] 成績\n[4] 登出\n")],
        ["把輸出迴圈放入 show_menu。", "定義函數後別忘記呼叫。"],
    ),
    task(
        "整理作品口頭展示提綱", "讀取以 | 分隔的作品名稱、學到的事及下一步。", "分三行加上「作品、學到、下一步」標籤。",
        "project, learned, next_step = [item.strip() for item in input().split('|')]\nprint(f'作品：{project}')\nprint(f'學到：{learned}')\nprint(f'下一步：{next_step}')\n",
        [("猜數字|條件判斷|加入難度\n", "作品：猜數字\n學到：條件判斷\n下一步：加入難度\n"), ("記帳工具|清單處理|儲存資料\n", "作品：記帳工具\n學到：清單處理\n下一步：儲存資料\n"), ("小測驗|函數拆分|增加題庫\n", "作品：小測驗\n學到：函數拆分\n下一步：增加題庫\n")],
        ["split('|') 可拆出三個欄位。", "逐行使用對應的繁體中文標籤。"],
    ),
]


JS_TASKS = [
    task(
        "建立 HTML 文件基本骨架", "第一行是網頁語言代碼，第二行是頁面標題。", "輸出正確的 HTML5 doctype、帶 lang 的 html 根元素，以及放在 head 內的 title。",
        "const fs = require('fs');\nconst [language, ...titleLines] = fs.readFileSync(0, 'utf8').trimEnd().split(/\\r?\\n/);\nconst title = titleLines.join('\\n');\nconsole.log('<!doctype html>');\nconsole.log(`<html lang=\"${language}\">`);\nconsole.log(`<head><title>${title}</title></head>`);\nconsole.log('</html>');\n",
        [("zh-Hant\n我的學習頁\n", "<!doctype html>\n<html lang=\"zh-Hant\">\n<head><title>我的學習頁</title></head>\n</html>\n"), ("en\nSchool Project\n", "<!doctype html>\n<html lang=\"en\">\n<head><title>School Project</title></head>\n</html>\n"), ("pt\n澳門文化\n", "<!doctype html>\n<html lang=\"pt\">\n<head><title>澳門文化</title></head>\n</html>\n")],
        ["HTML5 的 doctype 固定是 <!doctype html>。", "lang 屬性來自第一行，title 內容來自第二行。"],
    ),
    task(
        "組合網站檔名與副檔名", "讀取以空格分隔的檔名主體及副檔名。", "輸出「主體.副檔名」。",
        "const fs = require('fs');\nconst [name, extension] = fs.readFileSync(0, 'utf8').trim().split(/\\s+/);\nconsole.log(`${name}.${extension}`);\n",
        [("index html\n", "index.html\n"), ("styles css\n", "styles.css\n"), ("app js\n", "app.js\n")],
        ["用空白把輸入拆成兩部分。", "模板字串可插入點號。"],
    ),
    task(
        "把文字包進 HTML 標籤", "第一行是標籤名，第二行是文字內容。", "輸出完整的開始標籤、內容及結束標籤。",
        "const fs = require('fs');\nconst [tag, ...rest] = fs.readFileSync(0, 'utf8').trimEnd().split(/\\r?\\n/);\nconsole.log(`<${tag}>${rest.join('\\n')}</${tag}>`);\n",
        [("h1\n作品集\n", "<h1>作品集</h1>\n"), ("p\n歡迎來到我的網站\n", "<p>歡迎來到我的網站</p>\n"), ("strong\n重要提示\n", "<strong>重要提示</strong>\n")],
        ["先分開標籤名與內容。", "開始與結束標籤使用同一名稱。"],
    ),
    task(
        "產生開發工具狀態訊息", "讀取工具名稱及 ready 或 error 狀態，中間以空格分隔。", "輸出「工具名稱: 狀態」。",
        "const fs = require('fs');\nconst [tool, status] = fs.readFileSync(0, 'utf8').trim().split(/\\s+/);\nconsole.log(`${tool}: ${status}`);\n",
        [("DevTools ready\n", "DevTools: ready\n"), ("Console error\n", "Console: error\n"), ("Network ready\n", "Network: ready\n")],
        ["解構賦值可取得兩個欄位。", "不要把狀態寫死。"],
    ),
    task(
        "依層級建立標題元素", "第一行是 1 至 6 的標題層級，第二行是標題文字。", "輸出對應的 h1 至 h6 元素。",
        "const fs = require('fs');\nconst [level, ...text] = fs.readFileSync(0, 'utf8').trimEnd().split(/\\r?\\n/);\nconst value = text.join('\\n');\nconsole.log(`<h${level}>${value}</h${level}>`);\n",
        [("2\n關於我\n", "<h2>關於我</h2>\n"), ("1\n澳門景點\n", "<h1>澳門景點</h1>\n"), ("4\n聯絡方法\n", "<h4>聯絡方法</h4>\n")],
        ["層級會在開始及結束標籤使用。", "文字來自第二行。"],
    ),
    task(
        "建立可點擊的超連結", "第一行是網址，第二行是連結文字。", "輸出完整的 a 元素。",
        "const fs = require('fs');\nconst [url, ...label] = fs.readFileSync(0, 'utf8').trimEnd().split(/\\r?\\n/);\nconsole.log(`<a href=\"${url}\">${label.join('\\n')}</a>`);\n",
        [("https://example.com\n範例網站\n", "<a href=\"https://example.com\">範例網站</a>\n"), ("/about\n關於我們\n", "<a href=\"/about\">關於我們</a>\n"), ("mailto:school@example.test\n電郵\n", "<a href=\"mailto:school@example.test\">電郵</a>\n")],
        ["href 的值需要雙引號。", "連結文字放在兩個 a 標籤之間。"],
    ),
    task(
        "把清單資料轉成 li 元素", "讀取一行以逗號分隔的項目。", "每個項目各輸出一行 li 元素。",
        "const fs = require('fs');\nconst items = fs.readFileSync(0, 'utf8').trim().split(',').map(item => item.trim());\nconsole.log(items.map(item => `<li>${item}</li>`).join('\\n'));\n",
        [("HTML,CSS,JS\n", "<li>HTML</li>\n<li>CSS</li>\n<li>JS</li>\n"), ("首頁,課程\n", "<li>首頁</li>\n<li>課程</li>\n"), ("紅,綠,藍,黃\n", "<li>紅</li>\n<li>綠</li>\n<li>藍</li>\n<li>黃</li>\n")],
        ["先 split(',') 建立陣列。", "map() 可把每項變成標籤。"],
    ),
    task(
        "建立成對的語意標籤", "讀取一行以空格分隔的語意標籤名。", "每個名稱各輸出一行空的成對標籤。",
        "const fs = require('fs');\nconst tags = fs.readFileSync(0, 'utf8').trim().split(/\\s+/);\nconsole.log(tags.map(tag => `<${tag}></${tag}>`).join('\\n'));\n",
        [("main footer\n", "<main></main>\n<footer></footer>\n"), ("header nav\n", "<header></header>\n<nav></nav>\n"), ("article section aside\n", "<article></article>\n<section></section>\n<aside></aside>\n")],
        ["每個標籤都要有開始及結束部分。", "join('\\n') 可逐行輸出。"],
    ),
    task(
        "依類型建立 CSS 選擇器", "讀取 selector 類型 id、class 或 tag，以及名稱。", "id 加 #、class 加 .，tag 原樣輸出。",
        "const fs = require('fs');\nconst [type, name] = fs.readFileSync(0, 'utf8').trim().split(/\\s+/);\nconst prefix = type === 'id' ? '#' : type === 'class' ? '.' : '';\nconsole.log(prefix + name);\n",
        [("class card\n", ".card\n"), ("id app\n", "#app\n"), ("tag section\n", "section\n")],
        ["用條件選擇正確前綴。", "tag 類型沒有前綴。"],
    ),
    task(
        "建立 CSS 自訂屬性", "讀取變量名稱及顏色值，中間以空格分隔。", "輸出「--名稱: 值;」。",
        "const fs = require('fs');\nconst [name, value] = fs.readFileSync(0, 'utf8').trim().split(/\\s+/);\nconsole.log(`--${name}: ${value};`);\n",
        [("brand #2563eb\n", "--brand: #2563eb;\n"), ("accent tomato\n", "--accent: tomato;\n"), ("surface #f8fafc\n", "--surface: #f8fafc;\n")],
        ["自訂屬性以兩個連字號開始。", "結尾需要分號。"],
    ),
    task(
        "計算盒模型總寬度", "讀取 content、padding、border、margin 四個整數像素值。", "輸出包含左右兩側後的總寬度。",
        "const fs = require('fs');\nconst [content, padding, border, margin] = fs.readFileSync(0, 'utf8').trim().split(/\\s+/).map(Number);\nconsole.log(content + 2 * (padding + border + margin));\n",
        [("200 16 1 20\n", "274\n"), ("100 0 2 10\n", "124\n"), ("320 24 4 8\n", "392\n")],
        ["padding、border、margin 都有左右兩側。", "content 只加一次。"],
    ),
    task(
        "組合元素的偽類選擇器", "讀取基礎選擇器及偽類名稱。", "輸出「基礎選擇器:偽類」。",
        "const fs = require('fs');\nconst [selector, state] = fs.readFileSync(0, 'utf8').trim().split(/\\s+/);\nconsole.log(`${selector}:${state}`);\n",
        [(".button hover\n", ".button:hover\n"), ("a focus\n", "a:focus\n"), ("input checked\n", "input:checked\n")],
        ["偽類前使用冒號。", "基礎選擇器也來自輸入。"],
    ),
    task(
        "計算 Flex 項目之間的空隙", "讀取項目數量及 gap 像素。", "輸出所有項目之間的空隙總像素。",
        "const fs = require('fs');\nconst [count, gap] = fs.readFileSync(0, 'utf8').trim().split(/\\s+/).map(Number);\nconsole.log(Math.max(0, count - 1) * gap);\n",
        [("4 16\n", "48\n"), ("1 20\n", "0\n"), ("6 8\n", "40\n")],
        ["n 個項目之間只有 n-1 個空隙。", "單一項目沒有空隙。"],
    ),
    task(
        "產生 Grid 欄位設定", "讀取欄數及每欄單位。", "輸出 repeat(欄數, 單位)。",
        "const fs = require('fs');\nconst [columns, unit] = fs.readFileSync(0, 'utf8').trim().split(/\\s+/);\nconsole.log(`repeat(${columns}, ${unit})`);\n",
        [("3 1fr\n", "repeat(3, 1fr)\n"), ("2 minmax(0,1fr)\n", "repeat(2, minmax(0,1fr))\n"), ("5 120px\n", "repeat(5, 120px)\n")],
        ["columns 和 unit 都由輸入提供。", "留意逗號後的空格。"],
    ),
    task(
        "依畫面寬度判斷版面", "讀取畫面寬度；小於 600、600 至 1023、1024 以上。", "分別輸出 mobile、tablet 或 desktop。",
        "const fs = require('fs');\nconst width = Number(fs.readFileSync(0, 'utf8').trim());\nconsole.log(width < 600 ? 'mobile' : width < 1024 ? 'tablet' : 'desktop');\n",
        [("390\n", "mobile\n"), ("600\n", "tablet\n"), ("1024\n", "desktop\n"), ("599\n", "mobile\n")],
        ["由最小的範圍開始判斷。", "兩個邊界是 600 與 1024。"],
    ),
    task(
        "建立元件實例描述", "讀取元件名稱及實例數量。", "輸出「component:名稱×數量」。",
        "const fs = require('fs');\nconst [name, count] = fs.readFileSync(0, 'utf8').trim().split(/\\s+/);\nconsole.log(`component:${name}×${count}`);\n",
        [("card 3\n", "component:card×3\n"), ("button 2\n", "component:button×2\n"), ("badge 5\n", "component:badge×5\n")],
        ["元件名和數量都不可寫死。", "乘號使用 ×。"],
    ),
    task(
        "使用變量產生個人問候", "讀取一行使用者名稱。", "輸出「你好，名稱！」。",
        "const fs = require('fs');\nconst name = fs.readFileSync(0, 'utf8').trim();\nconsole.log(`你好，${name}！`);\n",
        [("Ada\n", "你好，Ada！\n"), ("小明\n", "你好，小明！\n"), ("Grace Hopper\n", "你好，Grace Hopper！\n")],
        ["把輸入存入 name 變量。", "使用全形逗號與感嘆號。"],
    ),
    task(
        "依分數作出通過判斷", "讀取分數及合格線兩個整數。", "分數不少於合格線輸出 pass，否則 retry。",
        "const fs = require('fs');\nconst [score, passing] = fs.readFileSync(0, 'utf8').trim().split(/\\s+/).map(Number);\nconsole.log(score >= passing ? 'pass' : 'retry');\n",
        [("72 60\n", "pass\n"), ("59 60\n", "retry\n"), ("80 80\n", "pass\n")],
        ["相等時也算通過。", "使用 >= 比較。"],
    ),
    task(
        "用迴圈計算陣列總和", "讀取一行以空格分隔的整數。", "輸出所有整數的總和。",
        "const fs = require('fs');\nconst numbers = fs.readFileSync(0, 'utf8').trim().split(/\\s+/).map(Number);\nlet total = 0;\nfor (const number of numbers) total += number;\nconsole.log(total);\n",
        [("1 2 3\n", "6\n"), ("10 -4 7\n", "13\n"), ("5 5 5 5\n", "20\n")],
        ["先把每個字串轉成 Number。", "迴圈內把數值累加到 total。"],
    ),
    task(
        "讀取物件中的作品資料", "讀取一行含 title 與 year 的 JSON 物件。", "輸出「title=標題,year=年份」。",
        "const fs = require('fs');\nconst project = JSON.parse(fs.readFileSync(0, 'utf8').trim());\nconsole.log(`title=${project.title},year=${project.year}`);\n",
        [("{\"title\":\"作品\",\"year\":2026}\n", "title=作品,year=2026\n"), ("{\"title\":\"校園網\",\"year\":2025}\n", "title=校園網,year=2025\n"), ("{\"title\":\"小遊戲\",\"year\":2027}\n", "title=小遊戲,year=2027\n")],
        ["使用 JSON.parse() 建立物件。", "以點記法讀取兩個屬性。"],
    ),
    task(
        "模擬選取符合條件的元素", "第一行以逗號列出 type:name 形式的元素，type 可為 id、class 或 tag；第二行是 #id、.class 或 tag 選擇器。", "輸出符合選擇器的元素數量。",
        "const fs = require('fs');\nconst [rawElements, selector] = fs.readFileSync(0, 'utf8').trim().split(/\\r?\\n/);\nconst elements = rawElements.split(',').map(item => {\n  const [type, name] = item.trim().split(':');\n  return { type, name };\n});\nconst wantedType = selector.startsWith('#') ? 'id' : selector.startsWith('.') ? 'class' : 'tag';\nconst wantedName = selector.replace(/^[#.]/, '');\nconsole.log(elements.filter(item => item.type === wantedType && item.name === wantedName).length);\n",
        [("id:app,class:card,class:card\n.card\n", "2\n"), ("id:app,class:panel,tag:main\n#app\n", "1\n"), ("tag:section,tag:p,class:note\nsection\n", "1\n"), ("class:item,id:item,tag:item\n.item\n", "1\n")],
        ["先由選擇器的第一個字符判斷 id、class 或 tag。", "類型和名稱都相同才算符合。"],
        difficulty="medium",
    ),
    task(
        "模擬 classList 狀態操作", "第一行是元素目前的 class 清單；第二行是 add、remove 或 toggle 加上一個 class 名。", "依操作更新後，以原有順序輸出 class 清單。",
        "const fs = require('fs');\nconst [rawClasses, command] = fs.readFileSync(0, 'utf8').trim().split(/\\r?\\n/);\nconst classes = rawClasses.trim() ? rawClasses.trim().split(/\\s+/) : [];\nconst [action, className] = command.trim().split(/\\s+/);\nconst index = classes.indexOf(className);\nif (action === 'add' && index === -1) classes.push(className);\nif (action === 'remove' && index !== -1) classes.splice(index, 1);\nif (action === 'toggle') {\n  if (index === -1) classes.push(className);\n  else classes.splice(index, 1);\n}\nconsole.log(classes.join(' '));\n",
        [("card active\nremove active\n", "card\n"), ("button primary\nadd large\n", "button primary large\n"), ("panel open\ntoggle open\n", "panel\n"), ("menu\ntoggle visible\n", "menu visible\n")],
        ["先用 indexOf 檢查目標 class 是否存在。", "toggle 存在時移除，不存在時加入。"],
        difficulty="medium",
    ),
    task(
        "統計事件清單中的點擊", "讀取一行以空格分隔的事件名稱。", "輸出 click 出現的次數。",
        "const fs = require('fs');\nconst events = fs.readFileSync(0, 'utf8').trim().split(/\\s+/);\nconsole.log(events.filter(event => event === 'click').length);\n",
        [("click input click\n", "2\n"), ("keydown click change click click\n", "3\n"), ("submit input\n", "0\n")],
        ["filter() 可保留指定事件。", "陣列 length 就是次數。"],
    ),
    task(
        "更新互動計數器", "讀取目前數值及變化量兩個整數。", "輸出更新後的計數。",
        "const fs = require('fs');\nconst [current, change] = fs.readFileSync(0, 'utf8').trim().split(/\\s+/).map(Number);\nconsole.log(current + change);\n",
        [("3 2\n", "5\n"), ("10 -4\n", "6\n"), ("0 7\n", "7\n")],
        ["變化量可能是負數。", "先轉成 Number 再相加。"],
    ),
    task(
        "把表單欄位序列化成查詢字串", "第一行是逗號分隔的欄位名，第二行是對應的欄位值。", "逐項使用 encodeURIComponent 編碼，並以 & 組成查詢字串。",
        "const fs = require('fs');\nconst [rawNames, rawValues] = fs.readFileSync(0, 'utf8').trimEnd().split(/\\r?\\n/);\nconst names = rawNames.split(',').map(item => item.trim());\nconst values = rawValues.split(',').map(item => item.trim());\nconsole.log(names.map((name, index) => `${encodeURIComponent(name)}=${encodeURIComponent(values[index])}`).join('&'));\n",
        [("name,grade\nAmy,6\n", "name=Amy&grade=6\n"), ("topic,note\nweb,hello world\n", "topic=web&note=hello%20world\n"), ("tag,email\na+b,student@example.test\n", "tag=a%2Bb&email=student%40example.test\n")],
        ["欄位名與欄位值的索引要互相對應。", "網址中的空格、加號與 @ 等字符需要編碼。"],
        difficulty="medium",
    ),
    task(
        "驗證表單文字長度", "第一行是最少字數，第二行是表單文字。", "字數足夠輸出 ok，否則輸出 short。",
        "const fs = require('fs');\nconst [minimum, ...text] = fs.readFileSync(0, 'utf8').trimEnd().split(/\\r?\\n/);\nconsole.log(text.join('\\n').length >= Number(minimum) ? 'ok' : 'short');\n",
        [("5\nhello\n", "ok\n"), ("6\nMacau\n", "short\n"), ("4\n學習程式\n", "ok\n")],
        ["中文字符的 length 也可直接比較。", "相等時符合最低要求。"],
    ),
    task(
        "驗證並更新畫面狀態", "讀取目前狀態及動作：viewing 可 edit；editing 可 save 或 cancel；saved 可 edit。", "合法時輸出 state=新狀態，不合法時輸出 invalid。",
        "const fs = require('fs');\nconst [current, action] = fs.readFileSync(0, 'utf8').trim().split(/\\s+/);\nconst transitions = {\n  viewing: { edit: 'editing' },\n  editing: { save: 'saved', cancel: 'viewing' },\n  saved: { edit: 'editing' },\n};\nconst next = transitions[current]?.[action];\nconsole.log(next ? `state=${next}` : 'invalid');\n",
        [("viewing edit\n", "state=editing\n"), ("editing save\n", "state=saved\n"), ("editing cancel\n", "state=viewing\n"), ("viewing save\n", "invalid\n"), ("saved cancel\n", "invalid\n")],
        ["使用巢狀物件表示每個 current state 允許的動作。", "找不到轉換規則時不要猜測，輸出 invalid。"],
        difficulty="medium",
    ),
    task(
        "更新本機偏好設定", "第一行是代表現有偏好設定的 JSON 物件；第二行是 set key value 或 remove key。", "套用操作後，依 key 字母順序逐行輸出 key=value。",
        "const fs = require('fs');\nconst [rawPreferences, command] = fs.readFileSync(0, 'utf8').trim().split(/\\r?\\n/);\nconst preferences = JSON.parse(rawPreferences);\nconst [action, key, ...valueParts] = command.trim().split(/\\s+/);\nif (action === 'set') preferences[key] = valueParts.join(' ');\nif (action === 'remove') delete preferences[key];\nconsole.log(Object.keys(preferences).sort().map(name => `${name}=${preferences[name]}`).join('\\n'));\n",
        [("{\"theme\":\"light\"}\nset theme dark\n", "theme=dark\n"), ("{\"font\":\"small\",\"theme\":\"dark\"}\nset language zh-Hant\n", "font=small\nlanguage=zh-Hant\ntheme=dark\n"), ("{\"font\":\"large\",\"theme\":\"light\"}\nremove font\n", "theme=light\n")],
        ["JSON.parse() 可還原現有設定。", "set 更新值；remove 使用 delete；最後排序 keys。"],
        difficulty="medium",
    ),
    task(
        "讀取 JSON 卡片內容", "讀取一行含 title 與 likes 的 JSON。", "輸出「標題｜likes 個讚」。",
        "const fs = require('fs');\nconst card = JSON.parse(fs.readFileSync(0, 'utf8').trim());\nconsole.log(`${card.title}｜${card.likes} 個讚`);\n",
        [("{\"title\":\"卡片\",\"likes\":3}\n", "卡片｜3 個讚\n"), ("{\"title\":\"澳門塔\",\"likes\":12}\n", "澳門塔｜12 個讚\n"), ("{\"title\":\"學習筆記\",\"likes\":0}\n", "學習筆記｜0 個讚\n")],
        ["先用 JSON.parse()。", "從 card 讀取 title 和 likes。"],
    ),
    task(
        "標準化 API 資源路徑", "讀取可能有多餘斜線的資源路徑。", "移除兩端斜線、把內部連續斜線縮成一個，再輸出以 /api/ 開始的路徑。",
        "const fs = require('fs');\nconst resource = fs.readFileSync(0, 'utf8').trim().replace(/\\/+/g, '/').replace(/^\\/+|\\/+$/g, '');\nconsole.log(`/api/${resource}`);\n",
        [("posts\n", "/api/posts\n"), ("/users/\n", "/api/users\n"), ("courses//12\n", "/api/courses/12\n"), ("//classes///7/tasks//\n", "/api/classes/7/tasks\n")],
        ["先用全域正則把每段連續斜線換成一個。", "再移除路徑兩端斜線，最後加上 /api/。"],
        difficulty="medium",
    ),
    task(
        "比較順序與並行等待時間", "第一行是 sequential 或 parallel，第二行是多個非同步任務的毫秒數。", "sequential 輸出全部時間總和；parallel 輸出最慢任務時間，結果加上 ms。",
        "const fs = require('fs');\nconst [mode, rawTimes] = fs.readFileSync(0, 'utf8').trim().split(/\\r?\\n/);\nconst times = rawTimes.trim().split(/\\s+/).map(Number);\nconst duration = mode === 'parallel' ? Math.max(...times) : times.reduce((sum, value) => sum + value, 0);\nconsole.log(`${duration}ms`);\n",
        [("sequential\n100 200\n", "300ms\n"), ("parallel\n50 75 25\n", "75ms\n"), ("parallel\n0 120 80\n", "120ms\n"), ("sequential\n10 20 30\n", "60ms\n")],
        ["順序等待會累加所有時間。", "並行開始時，總時間由最慢完成的任務決定。"],
        difficulty="medium",
    ),
    task(
        "把狀態碼轉成提示訊息", "讀取 HTTP 狀態碼。", "200 輸出 success；404 輸出 not found；其他輸出 error:狀態碼。",
        "const fs = require('fs');\nconst code = Number(fs.readFileSync(0, 'utf8').trim());\nconsole.log(code === 200 ? 'success' : code === 404 ? 'not found' : `error:${code}`);\n",
        [("404\n", "not found\n"), ("200\n", "success\n"), ("503\n", "error:503\n")],
        ["先處理兩個指定狀態碼。", "其他狀態要保留原數字。"],
    ),
    task(
        "建立 transition 屬性", "讀取 CSS 屬性名稱及持續毫秒數。", "輸出「transition: 屬性 毫秒ms;」。",
        "const fs = require('fs');\nconst [property, duration] = fs.readFileSync(0, 'utf8').trim().split(/\\s+/);\nconsole.log(`transition: ${property} ${duration}ms;`);\n",
        [("opacity 200\n", "transition: opacity 200ms;\n"), ("transform 350\n", "transition: transform 350ms;\n"), ("color 120\n", "transition: color 120ms;\n")],
        ["屬性名與時間之間有空格。", "毫秒數後加 ms 和分號。"],
    ),
    task(
        "依輸入秒數完成倒數", "讀取一個正整數秒數。", "由該數字逐行倒數至 1，最後輸出 go。",
        "const fs = require('fs');\nconst start = Number(fs.readFileSync(0, 'utf8').trim());\nconst output = [];\nfor (let value = start; value >= 1; value--) output.push(value);\noutput.push('go');\nconsole.log(output.join('\\n'));\n",
        [("3\n", "3\n2\n1\ngo\n"), ("1\n", "1\ngo\n"), ("5\n", "5\n4\n3\n2\n1\ngo\n")],
        ["for 迴圈每次減一。", "倒數後才加入 go。"],
    ),
    task(
        "計算碰撞取得的遊戲分數", "讀取碰撞次數、每次分數及扣分。", "輸出「碰撞次數×每次分數−扣分」。",
        "const fs = require('fs');\nconst [hits, points, penalty] = fs.readFileSync(0, 'utf8').trim().split(/\\s+/).map(Number);\nconsole.log(hits * points - penalty);\n",
        [("5 5 0\n", "25\n"), ("8 10 15\n", "65\n"), ("3 20 10\n", "50\n")],
        ["先算乘法，再減去 penalty。", "三個輸入都要轉成 Number。"],
    ),
    task(
        "把鍵盤按鍵映射成操作", "讀取 Enter、Escape、ArrowUp 或其他按鍵。", "分別輸出 activate、close、previous 或 ignore。",
        "const fs = require('fs');\nconst key = fs.readFileSync(0, 'utf8').trim();\nconst actions = { Enter: 'activate', Escape: 'close', ArrowUp: 'previous' };\nconsole.log(actions[key] || 'ignore');\n",
        [("Enter\n", "activate\n"), ("Escape\n", "close\n"), ("ArrowUp\n", "previous\n"), ("Shift\n", "ignore\n")],
        ["使用物件儲存按鍵對照。", "找不到時輸出 ignore。"],
    ),
    task(
        "把網站任務排成藍圖", "讀取一行以逗號分隔的任務。", "轉成大寫後以「 -> 」連接。",
        "const fs = require('fs');\nconst steps = fs.readFileSync(0, 'utf8').trim().split(',').map(step => step.trim().toUpperCase());\nconsole.log(steps.join(' -> '));\n",
        [("idea,build,test,show\n", "IDEA -> BUILD -> TEST -> SHOW\n"), ("plan,code,review\n", "PLAN -> CODE -> REVIEW\n"), ("sketch,style,publish\n", "SKETCH -> STYLE -> PUBLISH\n")],
        ["map() 可逐項轉成大寫。", "join(' -> ') 負責連接。"],
    ),
    task(
        "統計頁面的 HTML 與 CSS 資源", "讀取 HTML 元素數及 CSS 規則數。", "輸出「assets=兩者總和」。",
        "const fs = require('fs');\nconst [elements, rules] = fs.readFileSync(0, 'utf8').trim().split(/\\s+/).map(Number);\nconsole.log(`assets=${elements + rules}`);\n",
        [("8 5\n", "assets=13\n"), ("1 0\n", "assets=1\n"), ("24 17\n", "assets=41\n")],
        ["先把兩個欄位轉成數字。", "總和前加上 assets=。"],
    ),
    task(
        "依檢查結果決定修正", "讀取錯誤數量。", "0 輸出 pass；大於 0 輸出「fix:數量」。",
        "const fs = require('fs');\nconst errors = Number(fs.readFileSync(0, 'utf8').trim());\nconsole.log(errors === 0 ? 'pass' : `fix:${errors}`);\n",
        [("0\n", "pass\n"), ("2\n", "fix:2\n"), ("7\n", "fix:7\n")],
        ["使用 === 0 檢查完全通過。", "需要修正時保留錯誤數。"],
    ),
    task(
        "整理靜態作品發布摘要", "讀取以 | 分隔的目標、示範重點、程式特色及下一步。", "依序輸出四行並加上 goal、demo、code、next 標籤。",
        "const fs = require('fs');\nconst [goal, demo, code, next] = fs.readFileSync(0, 'utf8').trim().split('|').map(item => item.trim());\nconsole.log(`goal:${goal}`);\nconsole.log(`demo:${demo}`);\nconsole.log(`code:${code}`);\nconsole.log(`next:${next}`);\n",
        [("作品集|導覽|Grid|加入表單\n", "goal:作品集\ndemo:導覽\ncode:Grid\nnext:加入表單\n"), ("校園網|活動頁|Flex|改善手機版\n", "goal:校園網\ndemo:活動頁\ncode:Flex\nnext:改善手機版\n"), ("旅遊頁|景點卡|語意標籤|發布上線\n", "goal:旅遊頁\ndemo:景點卡\ncode:語意標籤\nnext:發布上線\n")],
        ["使用 | 分割四個欄位。", "四行標籤順序不可調換。"],
    ),
]


def build_entry(course_uuid, language_id, identity, authored):
    activity_uuid, challenge_uuid, block_id, source_fingerprint = identity
    tests = []
    for test_index, (stdin, expected_stdout) in enumerate(authored["cases"]):
        tests.append(
            {
                "test_uuid": f"rewrite_v1_{challenge_uuid.removeprefix('challenge_')}_{test_index + 1}",
                "label": "範例測試" if test_index == 0 else f"隱藏測試 {test_index}",
                "stdin": stdin,
                "expected_stdout": expected_stdout,
                "visibility": "visible" if test_index == 0 else "hidden",
                "order": test_index,
            }
        )
    starter = (
        "# 讀取標準輸入，完成題目要求\n"
        "data = input()\n"
        "# TODO: 計算並輸出答案\n"
        if language_id == 71
        else "const fs = require('fs');\nconst input = fs.readFileSync(0, 'utf8').trim();\n// TODO: 計算並輸出答案\n"
    )
    return {
        "identity": {
            "course_uuid": course_uuid,
            "activity_uuid": activity_uuid,
            "challenge_uuid": challenge_uuid,
            "block_id": block_id,
            "language_id": language_id,
        },
        "source_fingerprint": source_fingerprint,
        "rewrite": {
            "title": authored["title"],
            "description": authored["description"],
            "starter_code": starter,
            "solution_code": authored["solution_code"],
            "hints": authored["hints"],
            "difficulty": authored["difficulty"],
            "tests": tests,
        },
    }


def build_manifest():
    if len(PYTHON_IDENTITIES) != len(PYTHON_TASKS) or len(JS_IDENTITIES) != len(JS_TASKS):
        raise RuntimeError(
            f"authoring mismatch: Python {len(PYTHON_IDENTITIES)}/{len(PYTHON_TASKS)}, "
            f"JavaScript {len(JS_IDENTITIES)}/{len(JS_TASKS)}"
        )
    rewrites = [
        build_entry(PYTHON_COURSE, 71, identity, authored)
        for identity, authored in zip(PYTHON_IDENTITIES, PYTHON_TASKS)
    ] + [
        build_entry(JS_COURSE, 63, identity, authored)
        for identity, authored in zip(JS_IDENTITIES, JS_TASKS)
    ]
    rewrites.sort(
        key=lambda entry: (
            entry["identity"]["course_uuid"],
            entry["identity"]["activity_uuid"],
            entry["identity"]["challenge_uuid"],
        )
    )
    return {
        "schema_version": 1,
        "inventory": {"total": 51, "languages": {"63": 40, "71": 11}},
        "rewrites": rewrites,
    }


def main():
    destination = Path(__file__).with_name("rewrite_manifest.v1.json")
    destination.write_text(
        json.dumps(build_manifest(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
