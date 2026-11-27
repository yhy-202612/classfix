using System;
using System.Collections;
using System.Collections.Generic;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;

namespace ClassroomGoose
{
    internal sealed class Reply
    {
        internal string Text { get; private set; }
        internal bool CanDeliver { get; private set; }

        internal Reply(string text, bool canDeliver)
        {
            Text = text;
            CanDeliver = canDeliver;
        }

        internal static Reply Blocked()
        {
            return new Reply("这条提问或回答不适合在教室大屏显示。可以换个问题再试。", false);
        }
    }

    internal sealed class DeepSeek : IDisposable
    {
        internal const int QuestionLimit = 2000;
        private static readonly Uri Endpoint = new Uri("https://api.deepseek.com/chat/completions");
        private readonly HttpClient client;

        internal DeepSeek()
        {
            ServicePointManager.SecurityProtocol |= SecurityProtocolType.Tls12;
            client = new HttpClient(new HttpClientHandler { AllowAutoRedirect = false });
            client.Timeout = TimeSpan.FromSeconds(90);
            client.MaxResponseContentBufferSize = 512 * 1024;
        }

        internal async Task<Reply> Ask(string question, string key, CancellationToken cancel)
        {
            var json = new JavaScriptSerializer();
            var payload = new
            {
                model = "deepseek-flash",
                stream = false,
                max_tokens = 900,
                thinking = new { type = "disabled" },
                safety_check = new { level = "high" },
                messages = new[]
                {
                    new { role = "system", content = "用适合中学生和课堂公开屏幕的中文回答。简洁，约300字以内。只输出可以直接阅读的纯文本。对不适合在教室展示的问题给出简短、有帮助的替代建议。" },
                    new { role = "user", content = question }
                }
            };

            try
            {
                using (var request = new HttpRequestMessage(HttpMethod.Post, Endpoint))
                {
                    request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", key);
                    request.Content = new StringContent(json.Serialize(payload), Encoding.UTF8, "application/json");
                    using (var response = await client.SendAsync(request, cancel).ConfigureAwait(false))
                    {
                        string body = await response.Content.ReadAsStringAsync().ConfigureAwait(false);
                        if (response.IsSuccessStatusCode)
                            return ParseReply(body);
                        if (response.StatusCode == HttpStatusCode.BadRequest &&
                            body.IndexOf("Content Exists Risk", StringComparison.OrdinalIgnoreCase) >= 0)
                            return Reply.Blocked();
                        if (response.StatusCode == HttpStatusCode.Unauthorized)
                            return new Reply("DeepSeek 没有接受这个 API 密钥。请在托盘里检查设置。", false);
                        if ((int)response.StatusCode == 429)
                            return new Reply("DeepSeek 请求较多，请稍后再试。", false);
                        return new Reply("DeepSeek 请求失败（HTTP " + (int)response.StatusCode + "）。", false);
                    }
                }
            }
            catch (OperationCanceledException)
            {
                if (cancel.IsCancellationRequested) throw;
                return new Reply("等待 DeepSeek 回答超时，请重试。", false);
            }
            catch (HttpRequestException)
            {
                return new Reply("连接不到 DeepSeek，请检查网络。", false);
            }
        }

        internal static Reply ParseReply(string raw)
        {
            try
            {
                var data = new JavaScriptSerializer().DeserializeObject(raw) as IDictionary<string, object>;
                var choices = Field(data, "choices") as IList;
                var first = choices != null && choices.Count > 0 ? choices[0] as IDictionary<string, object> : null;
                var message = Field(first, "message") as IDictionary<string, object>;
                var finish = Field(first, "finish_reason") as string;
                if (finish == "content_filter" || !String.IsNullOrWhiteSpace(Field(message, "refusal") as string))
                    return Reply.Blocked();

                string text = Field(message, "content") as string;
                if ((finish == "stop" || finish == "length") && !String.IsNullOrWhiteSpace(text))
                    return new Reply(text.Trim(), true);
            }
            catch (ArgumentException) { }
            catch (InvalidOperationException) { }

            return new Reply("DeepSeek 没有返回可展示的回答，请重试。", false);
        }

        private static object Field(IDictionary<string, object> data, string name)
        {
            object value;
            return data != null && data.TryGetValue(name, out value) ? value : null;
        }

        public void Dispose()
        {
            client.Dispose();
        }
    }
}
