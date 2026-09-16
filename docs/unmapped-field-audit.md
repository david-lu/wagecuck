# Unmapped-field review

Re-plans stored controls only. No live navigation, browser filling, model calls, server validation or submissions were performed. Missing snapshots lack select options/context.

Reviewed every unmapped control in the original report: **173 controls**, across **105 distinct question/type/requirement groups**.

Current planner outcomes: {'choice_or_control_unresolved': 20, 'planned': 54, 'unresolved': 81, 'missing_profile_value': 16, 'already_filled_or_group_option': 2}.

The original live report remains unchanged. `planned` means a profile mapping exists, not that an employer accepted the answer. Repeated radio/checkbox labels remain separate controls in the JSON.

| Question | Type | Status | Profile field |
|---|---|---|---|
| Which location are you applying for? | select | choice_or_control_unresolved | application.preferred_location |
| Current location No location found. Try entering a different locationLoading | text | planned | city |
| LinkedIn URL | text | planned | linkedin |
| Twitter URL | text | planned | twitter |
| GitHub URL | text | planned | github |
| Other website | text | planned | website |
| When would you be available to start? ✱ | text | planned | availability |
| Do you have a valid work permit/citizenship that allows you to legally work full-time (40 hours per week) in Canada? List it below. ✱ | text | unresolved | — |
| What is your location? | select | choice_or_control_unresolved | country |
| Select a university or college | combobox | planned | education.0.school |
| If your post-secondary is not listed above, please note it down below. | text | planned | education.0.school |
| Please indicate your anticipated Graduation Date (if still a student). ✱ | textarea | missing_profile_value | education.0.expected_graduation_date |
| Are you able to commit to the full 8-month term? If not, please specify the maximum term length you would be able to commit to. | textarea | unresolved | — |
| GitHub URL (if any) | textarea | planned | github |
| Pronouns | checkbox | unresolved | — |
| LinkedIn URL✱ | text | planned | linkedin |
| Do you, now or in the future, require sponsorship to work in Canada? ✱ | select | unresolved | — |
| How did you hear about PocketHealth? ✱ | radio | choice_or_control_unresolved | source |
| If you were referred by an Employee of PocketHealth, please provide the name of the person who referred you. | text | unresolved | — |
| When are you looking to start? ✱ | textarea | planned | availability |
| What salary range are you targeting? ✱ | textarea | planned | compensation_expectations |
| cover_letter | file | unresolved | — |
| Do you have a preferred pronoun? | text | missing_profile_value | application.pronouns |
| Are you authorized to work in the country in which you’re applying?* | combobox | unresolved | — |
| Do you know anyone or are you related to anyone who works at Capco? | text | unresolved | — |
| Capco employee email address | text | unresolved | — |
| Request for Accommodation* | combobox | unresolved | — |
| Request for Accommodation details. | text | unresolved | — |
| Capco Job Candidate Privacy Notice Acknowledgement* | combobox | unresolved | — |
| Can you work 4 days a week in the office?* | combobox | unresolved | — |
| School | combobox | planned | education.0.school |
| Degree | combobox | planned | education.0.degree |
| Discipline | combobox | planned | education.0.field |
| Where are you presently located? | text | planned | location |
| Are you comfortable using your own device? | text | unresolved | — |
| Select the technologies you have experience with: * | checkbox | unresolved | — |
| What was your bachelor's university degree result, or expected result if you have not yet graduated? Please include the grading system to help us understand your result e.g. ‘85 out of 100’, ‘2:1 (Grading system: first class, 2:1, 2:2, third class)’ or ‘GPA score of 3.8/4.0 (predicted)’. We have hired outstanding individuals who did not attend or complete university. If this describes you, please continue with your application and enter ‘no degree’. | text | missing_profile_value | education.0.result |
| How did you perform in mathematics at high school?* | combobox | unresolved | — |
| How did you perform in your native language at high school?* | combobox | unresolved | — |
| We require all colleagues to meet in person 2-4 times a year, at internal company events lasting between 1-2 weeks. We try to pick new and interesting locations that will likely require international travel and entry requirement visas and vaccinations. Are you willing and able to commit to this?* | combobox | unresolved | — |
| LinkedIn Profile  | text | planned | linkedin |
| Please confirm that you have read and agree to Canonical's Recruitment Privacy Notice and Privacy Policy.* | combobox | unresolved | — |
| In which country do you currently work?* | combobox | planned | application.current_work_country |
| Please share your rationale or evidence for the high school performance selections above. Make reference to  provincial, state or nation-wide scoring systems, rankings, or recognition awards, or to competitive or selective college entrance results such as SAT or ACT scores, JAMB, matriculation results, IB results etc. We recognise every system is different but we will ask you to justify your selections above. | textarea | unresolved | — |
| During this application process I agree to use only my own words. I understand that plagiarism, the use of AI or other generated content will disqualify my application.* | combobox | unresolved | — |
| In the past ten years, looking only at the time since you graduated your first undergraduate degree, how many companies have you worked for?* | combobox | unresolved | — |
| Which gender do you identify as?* | combobox | unresolved | — |
| Please indicate your nationality:* | combobox | unresolved | — |
| Please indicate your race or ethnicity:* | combobox | unresolved | — |
| LinkedIn Profile | text | planned | linkedin |
| Start typing... | combobox | unresolved | — |
| Cover Letter | file | unresolved | — |
| Cerebras products, software, source code, technology, and/or services are subject to the U.S. Export Administration Regulations (EAR). Access to controlled source code and technology by our employees may require prior authorization. Cerebras can be required to perform an export compliance assessment to review if an individual is a U.S. person, or may require a license from the U.S. Government in order to release source code and technology to employees who are not U.S. persons. In order to comply with these regulations (and for no other reason), we are asking you to confirm citizenship and permanent residency as defined below. Please check the field that applies to you: | radio | unresolved | — |
| Gender | radio | unresolved | — |
| Race | radio | unresolved | — |
| Veteran Status | radio | already_filled_or_group_option | screening.veteran_status |
| Veteran Status | radio | planned | screening.veteran_status |
| What are your preferred pronouns? | text | missing_profile_value | application.pronouns |
| What is your preferred name (if different from your full name)? | textarea | planned | application.preferred_name |
| What is your desired annual salary (CAD)? | number | planned | application.compensation.annual_target |
| What specifically at Relay interests you? | textarea | unresolved | — |
| How do you use AI in your professional or personal life? | textarea | planned | application.ai_usage |
| If you were referred by a Relay employee, please provide their name. | text | unresolved | — |
| Location of Residence and Language: | select | unresolved | — |
| Cover letter | file | unresolved | — |
| Linkedin link profile * | text | planned | linkedin |
| Where are you located? * | radio | choice_or_control_unresolved | country |
| How many years of experience do you have working with Golang? * | text | planned | experience.golang.years |
| What AWS services have you worked with directly? * | text | missing_profile_value | experience.aws.summary |
| Which channel led you to apply? * | radio | choice_or_control_unresolved | source |
| Date Available * | text | planned | availability |
| Desired Pay * | text | planned | compensation_expectations |
| LinkedIn URL * | text | planned | linkedin |
| ¿Tienes nivel de inglés Intermedio - avanzado? * | radio | unresolved | — |
| Work History* | textarea | planned | work_history |
| Cover Letter | textarea | missing_profile_value | application.cover_letter_text |
| What is your availability to start this role, and do you have a notice period?*Required | text | planned | availability |
| Where are you based?*Required | text | planned | location |
| Whats your current CTC, please mentioned fixed and variable part.*Required | text | unresolved | — |
| What are your salary expectations?*Required | text | planned | compensation_expectations |
| We are looking for immediate joiners, please let us know you LWD or your NP*Required | text | planned | availability |
| How many years of professional Python development experience do you have?*Required | number | planned | experience.python.years |
| How many years of experience do you have building production microservices and RESTful APIs?*Required | number | missing_profile_value | experience.microservices_rest.years |
| Do you have production experience with CI/CD pipelines using GitHub Actions or Azure DevOps?*Required | number | unresolved | — |
| How many years of hands-on Kubernetes experience do you have?*Required | number | missing_profile_value | experience.kubernetes.years |
| How many years of hands-on Terraform or other Infrastructure as Code experience do you have?*Required | number | missing_profile_value | experience.terraform.years |
| Do you have hands-on experience with GitHub or GitLab APIs for workflow automation?* Required | radio | missing_profile_value | experience.git_platform_apis.has_experience |
| How many years have you been working specifically with GenAI or Agentic AI?*Required | textarea | missing_profile_value | experience.genai.years |
| Locations* Required | checkbox | unresolved | — |
| Work history | textarea | planned | work_history |
| Drop your file or upload, Additional files | file | unresolved | — |
| Cover letter | textarea | missing_profile_value | application.cover_letter_text |
| Required.By submitting this application, I agree that I have read the Privacy Policy and confirm that ClearRoute store my personal details to be able to process my job application.* | checkbox | unresolved | — |
| Yes, ClearRoute can contact me directly about specific future job opportunities. | checkbox | planned | consents.future_opportunities |
| Address Line 2 | text | missing_profile_value | address_line2 |
| Personal Summary This section is optional. Use it to tell us a little more about yourself. | textarea | planned | application.personal_summary |
| 3.Questions | radio | unresolved | — |
| 4. Submit Application | checkbox | unresolved | — |
| Headline | text | planned | application.headline |
| Telephone country code | combobox | unresolved | — |
| Summary | textarea | planned | application.personal_summary |
| What are your compensation expectations? | text | planned | compensation_expectations |
| smsConsent | checkbox | planned | consents.sms |
| Desired Salary | text | planned | compensation_expectations |
| What is your current notice period?* | text | planned | notice_period |
