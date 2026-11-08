namespace Synthetic.Stale
{
    public class ProfileService
    {
        // v2 inserted a comment, shifting the assignment line
        public void UpdateName(string name)
        {
            var current = Load();
            // the source changed after the first discovery
            current.FullName = name;
            Save(current);
        }
    }
}
